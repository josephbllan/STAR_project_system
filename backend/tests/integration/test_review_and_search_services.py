import pytest

from apps.review.sanitisation import sanitise
from apps.review.services import decide_approval, record_note, record_rating, request_approval
from retrieval.encoders.clip import clip_ref, load_clip
from retrieval.encoders.dinov2 import dinov2_ref, load_dinov2
from tests.factories.accounts import ReviewerFactory
from tests.scenarios import completed_run

pytestmark = [pytest.mark.django_db]


def test_sanitise_strips_markup() -> None:
    assert sanitise("<b>safe</b> note") == "safe note"


def test_rating_and_note_and_approval_round_trip() -> None:
    bundle = completed_run
    result = bundle.results[0]
    author = ReviewerFactory()
    rating = record_rating(author=author, value=4, scope="result", result=result)
    note = record_note(author=author, body="looks like a trainer", scope="result", result=result)
    approval = request_approval(result=result, requested_by=bundle.run.created_by)
    decide_approval(approval, decided_by=author, approved=True)
    assert rating.value == 4
    assert "trainer" in note.body
    approval.refresh_from_db()
    assert approval.state == "approved"


def test_text_query_execution_writes_ranked_rows() -> None:
    from apps.cases.models import Query, QueryType, QueryStatus
    from apps.search.services import execute_query

    bundle = completed_run
    query = Query.objects.create(
        run=bundle.run,
        sequence=99,
        query_type=QueryType.TEXT,
        query_text="trainer",
    )
    written = execute_query(query)
    query.refresh_from_db()
    assert query.status == QueryStatus.COMPLETE
    assert query.result_count == len(written)


def test_save_run_marks_history() -> None:
    from apps.cases.services import save_run
    from tests.scenarios import completed_run

    bundle = completed_run()
    save_run(bundle.run)
    bundle.run.refresh_from_db()
    assert bundle.run.params.get("saved") is True


def test_create_run_honours_encoder_toggles() -> None:
    from apps.cases.services import create_run
    from tests.factories.accounts import InvestigatorFactory
    from tests.factories.cases import CaseFactory
    from tests.scenarios import encoder_pair

    pair = encoder_pair()
    pair.clip.is_active = True
    pair.clip.save(update_fields=["is_active"])
    pair.dinov2.is_active = True
    pair.dinov2.save(update_fields=["is_active"])
    owner = InvestigatorFactory()
    case = CaseFactory(owner=owner)
    run = create_run(case=case, created_by=owner, label="dino-only", use_clip=False, use_dinov2=True)
    assert run.clip_encoder_id is None
    assert run.dinov2_encoder_id is not None


def test_execute_query_uses_corpus_id_not_run_corpus_pk() -> None:
    """A run-corpus row's pk is not the corpus id. Using the junction pk dropped every hit."""
    from apps.cases.models import Query, QueryType, QueryStatus
    from apps.datasets.models import EvidenceState
    from apps.search.services import execute_query
    from tests.factories.cases import RunCorpusFactory, RunFactory
    from tests.scenarios import searchable_corpus

    corpus = searchable_corpus(size=3)
    for row in corpus.evidence:
        row.state = EvidenceState.INDEXED
        row.save(update_fields=["state"])

    for _ in range(12):
        RunCorpusFactory()

    run = RunFactory(
        clip_encoder=corpus.encoders.clip,
        dinov2_encoder=corpus.encoders.dinov2,
        top_k=5,
    )
    link = RunCorpusFactory(run=run, corpus=corpus.corpus)
    assert link.pk != corpus.corpus.pk

    query = Query.objects.create(
        run=run,
        sequence=1,
        query_type=QueryType.TEXT,
        query_text="trainer",
    )
    written = execute_query(query)
    query.refresh_from_db()
    assert query.status == QueryStatus.COMPLETE
    assert written
    assert {row.evidence_file.corpus_id for row in written} == {corpus.corpus.pk}


def test_execute_query_prefers_shared_indexed_file() -> None:
    from apps.cases.models import Query, QueryType
    from apps.datasets.models import EvidenceState
    from apps.search.services import execute_query
    from tests.factories.cases import CaseFactory, RunCorpusFactory, RunFactory
    from tests.factories.datasets import CorpusFactory, EvidenceFileFactory
    from tests.scenarios import searchable_corpus

    corpus = searchable_corpus(size=1)
    original = corpus.evidence[0]
    original.state = EvidenceState.INDEXED
    original.save(update_fields=["state"])
    case = CaseFactory()
    probe_copy = EvidenceFileFactory(
        content=original.content,
        corpus=CorpusFactory(),
        case=case,
        original_filename="probe-copy.jpg",
        state=EvidenceState.REGISTERED,
    )
    run = RunFactory(
        case=case,
        clip_encoder=corpus.encoders.clip,
        dinov2_encoder=corpus.encoders.dinov2,
        top_k=5,
    )
    RunCorpusFactory(run=run, corpus=corpus.corpus)
    RunCorpusFactory(run=run, corpus=probe_copy.corpus)
    query = Query.objects.create(
        run=run,
        sequence=1,
        query_type=QueryType.TEXT,
        query_text="trainer",
    )
    written = execute_query(query)
    assert written
    assert written[0].evidence_file_id == original.pk


def test_encoder_loaders_are_deterministic_without_weights() -> None:
    clip = load_clip()
    dino = load_dinov2()
    assert clip.ref.dimensions == clip_ref.dimensions
    assert dino.ref.dimensions == dinov2_ref.dimensions
    assert clip.encode_text("sole") != dino.encode_text("sole")
