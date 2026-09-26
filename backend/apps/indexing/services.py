"""Plan encoding work and persist vectors."""

from __future__ import annotations

from apps.common import storage
from apps.datasets.models import ContentObject, Corpus, EvidenceFile, EvidenceState
from apps.indexing.adapters import OrmVectorStore
from apps.search.models import ClipEmbedding, Dinov2Embedding, Encoder, EncoderFamily
from retrieval.encoders.clip import load_clip
from retrieval.encoders.dinov2 import load_dinov2
from retrieval.types import Embedding, Modality

BATCH_SIZE = 32


def content_needing_encoding(corpus: Corpus, encoder: Encoder) -> list[ContentObject]:
    encoded_ids = (
        ClipEmbedding.objects.filter(encoder=encoder).values_list("content_id", flat=True)
        if encoder.family == EncoderFamily.CLIP
        else Dinov2Embedding.objects.filter(encoder=encoder).values_list("content_id", flat=True)
    )
    return list(
        ContentObject.objects.filter(
            registrations__corpus=corpus,
            registrations__state__in=[EvidenceState.REGISTERED, EvidenceState.INDEXED],
        )
.exclude(pk__in=encoded_ids)
.distinct()
    )


def plan_batches(content: list[ContentObject]) -> list[list[int]]:
    return [
        [item.pk for item in content[i : i + BATCH_SIZE]]
        for i in range(0, len(content), BATCH_SIZE)
    ]


def encode_content(content: ContentObject, encoder: Encoder) -> Embedding | None:
    if not storage.exists(content.storage_key):
        return None
    try:
        with storage.open_stored(content.storage_key) as handle:
            data = handle.read()
        loaded = load_clip() if encoder.family == EncoderFamily.CLIP else load_dinov2()
        values = loaded.encode_image(data)
    except Exception:
        return None
    embedding = Embedding(
        content_id=content.public_id,
        encoder=loaded.ref,
        modality=Modality.IMAGE,
        values=values,
    )
    # The stored encoder row may use a different name than the loader default; persist under
    # the registration the planner selected.
    embedding = Embedding(
        content_id=content.public_id,
        encoder=type(loaded.ref)(name=encoder.name, dimensions=encoder.dimensions),
        modality=Modality.IMAGE,
        values=values,
    )
    OrmVectorStore().upsert(embedding)
    EvidenceFile.objects.filter(content=content, state=EvidenceState.REGISTERED).update(
        state=EvidenceState.INDEXED
    )
    return embedding
