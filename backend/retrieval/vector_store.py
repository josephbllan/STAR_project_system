"""The port through which vectors are read and written.

The retrieval package does not know PostgreSQL. An adapter in `apps.indexing` implements this
protocol against the two embedding tables. Tests can implement it in memory.
"""

from __future__ import annotations

from typing import Protocol
from uuid import UUID

from retrieval.types import Embedding, EncoderRef, ScoredCandidate


class VectorStore(Protocol):
    def upsert(self, embedding: Embedding) -> None:...

    def get(self, content_id: UUID, encoder: EncoderRef) -> Embedding | None:...

    def search(
        self,
        query: tuple[float,...],
        encoder: EncoderRef,
        *,
        limit: int,
        content_ids: set[UUID] | None = None,
    ) -> list[ScoredCandidate]:...
