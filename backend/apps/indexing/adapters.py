"""`VectorStore` against the two embedding tables."""

from __future__ import annotations

from uuid import UUID

from pgvector.django import CosineDistance

from apps.search.models import (
    CLIP_DIMENSIONS,
    ClipEmbedding,
    Dinov2Embedding,
    Encoder,
)
from retrieval.normalisation import l2_normalise
from retrieval.types import Embedding, EncoderRef, Modality, ScoredCandidate


def _model_for(encoder: Encoder | EncoderRef):
    dimensions = encoder.dimensions if isinstance(encoder, EncoderRef) else encoder.dimensions
    return ClipEmbedding if dimensions == CLIP_DIMENSIONS else Dinov2Embedding


def _resolve_encoder(encoder: Encoder | EncoderRef) -> Encoder:
    if isinstance(encoder, Encoder):
        return encoder
    qs = Encoder.objects.filter(name=encoder.name, dimensions=encoder.dimensions)
    return qs.filter(is_active=True).first() or qs.latest("pk")


class OrmVectorStore:
    def upsert(self, embedding: Embedding) -> None:
        values = list(l2_normalise(embedding.values))
        encoder = _resolve_encoder(embedding.encoder)
        model = _model_for(embedding.encoder)
        from apps.datasets.models import ContentObject

        content = ContentObject.objects.get(public_id=embedding.content_id)
        model.objects.update_or_create(
            content=content,
            encoder=encoder,
            defaults={"embedding": values},
        )

    def get(self, content_id: UUID, encoder: EncoderRef) -> Embedding | None:

        model = _model_for(encoder)
        row = (
            model.objects.filter(content__public_id=content_id, encoder=_resolve_encoder(encoder))
.select_related("content", "encoder")
.first()
        )
        if row is None:
            return None
        return Embedding(
            content_id=row.content.public_id,
            encoder=EncoderRef(name=row.encoder.name, dimensions=row.encoder.dimensions),
            modality=Modality.IMAGE,
            values=tuple(row.embedding),
        )

    def search(
        self,
        query: tuple[float,...],
        encoder: EncoderRef,
        *,
        limit: int,
        content_ids: set[UUID] | None = None,
    ) -> list[ScoredCandidate]:
        model = _model_for(encoder)
        qs = model.objects.filter(encoder=_resolve_encoder(encoder)).select_related("content", "encoder")
        if content_ids is not None:
            qs = qs.filter(content__public_id__in=content_ids)
        qs = qs.annotate(distance=CosineDistance("embedding", list(query))).order_by("distance")[
            :limit
        ]
        results = []
        for row in qs:
            # cosine similarity = 1 - cosine distance for unit vectors
            score = 1.0 - float(row.distance)
            results.append(
                ScoredCandidate(
                    content_id=row.content.public_id,
                    encoder=EncoderRef(name=row.encoder.name, dimensions=row.encoder.dimensions),
                    score=score,
                )
            )
        return results
