from io import BytesIO
from pathlib import Path
from unittest.mock import patch
from uuid import NAMESPACE_URL, uuid5

import pytest
from PIL import Image

from apps.common import storage
from apps.datasets.models import EvidenceFile, EvidenceState, Mount, ScanStatus
from apps.datasets.services import scan_local_mount
from apps.indexing.services import BATCH_SIZE
from apps.indexing.tasks import encode_corpus
from apps.tasks.models import TaskRun, TaskStatus
from tests.factories.datasets import CorpusFactory, EvidenceFileFactory
from tests.factories.search import EncoderFactory
from tests.factories.tasks import TaskRunFactory

pytestmark = [pytest.mark.django_db]


def _jpeg(color: tuple[int, int, int] = (12, 80, 160)) -> bytes:
    image = Image.new("RGB", (32, 24), color)
    buffer = BytesIO()
    image.save(buffer, format="JPEG")
    return buffer.getvalue()


def _tree(root: Path) -> Path:
    nested = root / "case-a" / "prints"
    nested.mkdir(parents=True)
    (nested / "print.jpg").write_bytes(_jpeg())
    (root / "notes.txt").write_text("ignore")
    (root / ".hidden").mkdir()
    (root / ".hidden" / "skip.jpg").write_bytes(_jpeg((9, 9, 9)))
    return root


def test_iter_local_images_walks_subfolders_and_skips_the_rest(tmp_path, settings) -> None:
    settings.LOCAL_FOLDER_INGEST = True
    root = _tree(tmp_path / "participent")
    found = [path.name for path in storage.iter_local_images(root)]
    assert found == ["print.jpg"]


def test_resolve_local_folder_is_refused_when_ingest_is_off(tmp_path, settings) -> None:
    settings.LOCAL_FOLDER_INGEST = False
    folder = tmp_path / "closed"
    folder.mkdir()
    with pytest.raises(storage.InvalidLocalFolderError):
        storage.resolve_local_folder(str(folder))


@pytest.mark.parametrize("code", ["Ecom", "NDFsim", "PreviousCases"])
def test_scan_registers_nested_images_into_the_chosen_corpus(code, tmp_path, tmp_storage) -> None:
    corpus = CorpusFactory(code=code, name=code)
    root = _tree(tmp_path / code)
    mount = Mount.objects.create(corpus=corpus, path=str(root), label=code)
    result = scan_local_mount(mount)
    assert result == {"seen": 1, "errors": 0, "registered": 1}
    mount.refresh_from_db()
    assert mount.last_scan_status == ScanStatus.OK
    evidence = EvidenceFile.objects.get(corpus=corpus)
    assert evidence.original_filename == "print.jpg"
    assert "prints" in evidence.source_path


def test_a_second_scan_does_not_duplicate_registrations(tmp_path, tmp_storage) -> None:
    corpus = CorpusFactory()
    root = _tree(tmp_path / "again")
    mount = Mount.objects.create(corpus=corpus, path=str(root), label="again")
    scan_local_mount(mount)
    again = scan_local_mount(mount)
    assert again["registered"] == 0
    assert again["seen"] == 1
    assert EvidenceFile.objects.filter(corpus=corpus).count() == 1
    mount.refresh_from_db()
    assert mount.last_scan_status == ScanStatus.OK


def test_investigator_adds_a_local_folder_to_any_corpus(
    client_as, investigator, tmp_path, tmp_storage
) -> None:
    corpus = CorpusFactory(code="NDFsim", name="NDFsim")
    EncoderFactory()
    folder = tmp_path / "cast"
    folder.mkdir()
    (folder / "mark.jpg").write_bytes(_jpeg())
    response = client_as(investigator).post(
        "/api/v1/mounts/",
        {"corpus": corpus.pk, "path": str(folder), "label": "cast"},
        format="json",
    )
    assert response.status_code == 201
    assert response.data["corpus_code"] == "NDFsim"
    assert Mount.objects.filter(corpus=corpus, path=str(folder.resolve())).exists()


def test_analyst_cannot_add_a_folder(client_as, analyst, tmp_path) -> None:
    corpus = CorpusFactory()
    folder = tmp_path / "blocked"
    folder.mkdir()
    response = client_as(analyst).post(
        "/api/v1/mounts/",
        {"corpus": corpus.pk, "path": str(folder)},
        format="json",
    )
    assert response.status_code == 403


def test_add_folder_refuses_when_local_ingest_is_off(
    client_as, investigator, tmp_path, settings
) -> None:
    settings.LOCAL_FOLDER_INGEST = False
    corpus = CorpusFactory()
    folder = tmp_path / "off"
    folder.mkdir()
    response = client_as(investigator).post(
        "/api/v1/mounts/",
        {"corpus": corpus.pk, "path": str(folder)},
        format="json",
    )
    assert response.status_code == 400


def test_coverage_reports_registered_and_indexed_counts(client_as, investigator) -> None:
    corpus = CorpusFactory(code="NDFsim", name="NDFsim")
    for _ in range(3):
        EvidenceFileFactory(corpus=corpus)
    EvidenceFileFactory(corpus=corpus, state=EvidenceState.INDEXED)
    response = client_as(investigator).get("/api/v1/corpora/coverage/")
    assert response.status_code == 200
    row = next(item for item in response.data["results"] if item["code"] == "NDFsim")
    assert row["evidence_count"] == 4
    assert row["indexed_count"] == 1


def test_investigator_can_start_indexing(client_as, investigator) -> None:
    corpus = CorpusFactory(code="Ecom", name="Ecom")
    EncoderFactory()
    response = client_as(investigator).post(f"/api/v1/corpora/{corpus.pk}/encode/")
    assert response.status_code == 202
    assert response.data["runs"]


def test_encode_corpus_dispatches_every_batch_not_only_the_first() -> None:
    corpus = CorpusFactory()
    encoder = EncoderFactory()
    pending = BATCH_SIZE * 2 + 5
    for _ in range(pending):
        EvidenceFileFactory(corpus=corpus)
    parent = TaskRunFactory(task_name="indexing.encode_corpus", max_attempts=1)
    with patch("apps.tasks.dispatch._enqueue"):
        encode_corpus(
            task_run_id=str(parent.public_id),
            corpus_id=corpus.pk,
            encoder_id=encoder.pk,
        )
    assert TaskRun.objects.filter(task_name="indexing.encode_batch").count() == 3
    parent.refresh_from_db()
    assert parent.progress_total == pending
    assert parent.status == TaskStatus.STARTED


@pytest.mark.django_db(transaction=True)
def test_start_indexing_requeues_an_orphaned_encode_corpus(client_as, investigator) -> None:
    corpus = CorpusFactory()
    encoder = EncoderFactory()
    target = uuid5(NAMESPACE_URL, f"encode:{corpus.pk}:{encoder.pk}")
    parent = TaskRunFactory(
        task_name="indexing.encode_corpus",
        status=TaskStatus.STARTED,
        progress_current=32,
        progress_total=80,
        target_public_id=target,
        max_attempts=1,
    )
    with patch("apps.tasks.dispatch._enqueue") as enqueue:
        response = client_as(investigator).post(f"/api/v1/corpora/{corpus.pk}/encode/")
    assert response.status_code == 202
    assert response.data["already_in_progress"] is False
    assert enqueue.called
    assert (
        TaskRun.objects.filter(
            task_name="indexing.encode_corpus", target_public_id=target
        ).count()
        == 1
    )
    assert any(call.args[2] == parent.pk for call in enqueue.call_args_list)
