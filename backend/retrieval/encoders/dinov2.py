"""DINOv2 encoder. Same loading rule as CLIP: weights optional, framework never imported."""

from __future__ import annotations

from retrieval.encoders.deterministic import DeterministicEncoder
from retrieval.encoders.neural import try_load_dinov2
from retrieval.types import EncoderRef

DINOV2_NAME = "vit_small_patch14_dinov2.lvd142m"
DINOV2_DIMENSIONS = 384


def load_dinov2(*, weights_path: str | None = None):
    loaded = try_load_dinov2(weights_path)
    if loaded is not None:
        return loaded
    return DeterministicEncoder(DINOV2_NAME, DINOV2_DIMENSIONS)


def dinov2_ref() -> EncoderRef:
    return EncoderRef(name=DINOV2_NAME, dimensions=DINOV2_DIMENSIONS)
