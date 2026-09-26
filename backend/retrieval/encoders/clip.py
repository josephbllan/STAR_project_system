"""CLIP encoder. Weights are loaded only when asked; the module itself imports no framework."""

from __future__ import annotations

from retrieval.encoders.deterministic import DeterministicEncoder
from retrieval.encoders.neural import try_load_clip
from retrieval.types import EncoderRef

CLIP_NAME = "openai/clip-vit-base-patch32"
CLIP_DIMENSIONS = 512


def load_clip(*, weights_path: str | None = None):
    """Return a CLIP-shaped encoder.

    Real `transformers` weights are used when the `ml` extra is installed. Otherwise the
    deterministic stand-in keeps the pipeline testable without a PyTorch import.
    """
    loaded = try_load_clip(weights_path)
    if loaded is not None:
        return loaded
    return DeterministicEncoder(CLIP_NAME, CLIP_DIMENSIONS)


def clip_ref() -> EncoderRef:
    return EncoderRef(name=CLIP_NAME, dimensions=CLIP_DIMENSIONS)
