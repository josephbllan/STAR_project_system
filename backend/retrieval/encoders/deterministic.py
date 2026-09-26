"""A framework-free encoder whose output is a function of the bytes and the dimensionality.

Used wherever a real CLIP or DINOv2 weight file is not loaded: unit tests, continuous
integration, and local development without a GPU. The vector is L2-normalised, so cosine
distance is well-defined, and it is stable across runs, so a retrieval test can assert ranks.
"""

from __future__ import annotations

import hashlib
from uuid import UUID

from retrieval.normalisation import DegenerateVectorError, l2_normalise
from retrieval.types import Embedding, EncoderRef, Modality


class DeterministicEncoder:
    def __init__(self, name: str, dimensions: int) -> None:
        self._ref = EncoderRef(name=name, dimensions=dimensions)

    @property
    def ref(self) -> EncoderRef:
        return self._ref

    def encode_image(self, data: bytes) -> tuple[float,...]:
        return self._from_digest(hashlib.sha256(data).digest())

    def encode_text(self, text: str) -> tuple[float,...]:
        return self._from_digest(hashlib.sha256(text.encode()).digest())

    def embedding_for(
        self, content_id: UUID, values: tuple[float,...], *, modality: Modality
    ) -> Embedding:
        return Embedding(content_id=content_id, encoder=self.ref, modality=modality, values=values)

    def _from_digest(self, digest: bytes) -> tuple[float,...]:
        width = self._ref.dimensions
        raw = []
        seed = digest
        while len(raw) < width:
            seed = hashlib.sha256(seed).digest()
            raw.extend((b / 255.0) - 0.5 for b in seed)
        try:
            return l2_normalise(raw[:width])
        except DegenerateVectorError as exc:
            raise ValueError("deterministic encoder produced a zero vector") from exc
