"""The encoder interface the rest of the system depends on.

Nothing here imports a framework. An implementation that cannot produce a vector raises
`EncoderError` rather than returning a zero vector: a zero vector is equidistant from everything
and must never be persisted.
"""

from __future__ import annotations

from typing import Protocol

from retrieval.types import Embedding, EncoderRef, Modality


class EncoderError(Exception):
    """The encoder could not produce a vector for this input."""


class Encoder(Protocol):
    @property
    def ref(self) -> EncoderRef:...

    def encode_image(self, data: bytes) -> tuple[float,...]:...

    def encode_text(self, text: str) -> tuple[float,...]:...

    def embedding_for(
        self, content_id, values: tuple[float,...], *, modality: Modality
    ) -> Embedding:
        return Embedding(content_id=content_id, encoder=self.ref, modality=modality, values=values)
