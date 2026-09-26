from io import BytesIO

import pytest
from PIL import Image

from apps.common import storage
from apps.datasets.models import EvidenceState
from apps.datasets.services import AlreadyRegisteredError, register_bytes
from tests.factories.datasets import CorpusFactory

pytestmark = [pytest.mark.django_db]


def _jpeg(color: tuple[int, int, int] = (12, 80, 160)) -> bytes:
    image = Image.new("RGB", (32, 24), color)
    buffer = BytesIO()
    image.save(buffer, format="JPEG")
    return buffer.getvalue()


def test_ingestion_stores_bytes_then_registers(tmp_storage) -> None:
    corpus = CorpusFactory()
    evidence = register_bytes(_jpeg(), corpus=corpus, original_filename="trainer.jpg")
    assert evidence.state == EvidenceState.REGISTERED
    assert storage.exists(evidence.content.storage_key)
    assert evidence.derived_artifacts.filter(kind="thumbnail").exists()


def test_the_same_bytes_in_the_same_corpus_are_refused(tmp_storage) -> None:
    corpus = CorpusFactory()
    data = _jpeg()
    register_bytes(data, corpus=corpus)
    with pytest.raises(AlreadyRegisteredError):
        register_bytes(data, corpus=corpus)


def test_the_same_bytes_in_a_second_corpus_share_content(tmp_storage) -> None:
    data = _jpeg((200, 10, 10))
    first = register_bytes(data, corpus=CorpusFactory())
    second = register_bytes(data, corpus=CorpusFactory())
    assert first.content_id == second.content_id
    assert first.pk != second.pk
