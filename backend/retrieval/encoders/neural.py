"""Real CLIP / DINOv2 backends. Torch is imported only when a model is loaded."""

from __future__ import annotations

import io
import os
from functools import lru_cache

from retrieval.encoders.base import EncoderError
from retrieval.normalisation import l2_normalise
from retrieval.types import Embedding, EncoderRef, Modality

CLIP_HF_ID = "openai/clip-vit-base-patch32"
DINOV2_HF_ID = "facebook/dinov2-small"
CLIP_DIMENSIONS = 512
DINOV2_DIMENSIONS = 384


def _device():
    import torch

    return "cuda" if torch.cuda.is_available() else "cpu"


def _open_rgb(data: bytes):
    from PIL import Image

    return Image.open(io.BytesIO(data)).convert("RGB")


class NeuralClip:
    def __init__(self, model, processor, device: str) -> None:
        self._model = model
        self._processor = processor
        self._device = device
        self._ref = EncoderRef(CLIP_HF_ID, CLIP_DIMENSIONS)

    @property
    def ref(self) -> EncoderRef:
        return self._ref

    def encode_image(self, data: bytes) -> tuple[float, ...]:
        import torch

        image = _open_rgb(data)
        inputs = self._processor(images=image, return_tensors="pt")
        inputs = {key: value.to(self._device) for key, value in inputs.items()}
        with torch.no_grad():
            features = self._model.get_image_features(**inputs)[0]
        return l2_normalise(features.detach().cpu().tolist())

    def encode_text(self, text: str) -> tuple[float, ...]:
        import torch

        inputs = self._processor(text=[text], return_tensors="pt", padding=True, truncation=True)
        inputs = {key: value.to(self._device) for key, value in inputs.items()}
        with torch.no_grad():
            features = self._model.get_text_features(**inputs)[0]
        return l2_normalise(features.detach().cpu().tolist())

    def embedding_for(self, content_id, values, *, modality: Modality) -> Embedding:
        return Embedding(content_id=content_id, encoder=self.ref, modality=modality, values=values)


class NeuralDinov2:
    def __init__(self, model, processor, device: str) -> None:
        self._model = model
        self._processor = processor
        self._device = device
        self._ref = EncoderRef(DINOV2_HF_ID, DINOV2_DIMENSIONS)

    @property
    def ref(self) -> EncoderRef:
        return self._ref

    def encode_image(self, data: bytes) -> tuple[float, ...]:
        import torch

        image = _open_rgb(data)
        inputs = self._processor(images=image, return_tensors="pt")
        inputs = {key: value.to(self._device) for key, value in inputs.items()}
        with torch.no_grad():
            hidden = self._model(**inputs).last_hidden_state[0, 0]
        return l2_normalise(hidden.detach().cpu().tolist())

    def encode_text(self, text: str) -> tuple[float, ...]:
        from retrieval.encoders.deterministic import DeterministicEncoder

        return DeterministicEncoder(DINOV2_HF_ID, DINOV2_DIMENSIONS).encode_text(text)

    def embedding_for(self, content_id, values, *, modality: Modality) -> Embedding:
        return Embedding(content_id=content_id, encoder=self.ref, modality=modality, values=values)


@lru_cache(maxsize=1)
def try_load_clip(weights_path: str | None = None) -> NeuralClip | None:
    try:
        import torch
        from transformers import CLIPModel, CLIPProcessor
    except ImportError:
        return None
    name = weights_path or os.environ.get("SHOERAG_CLIP_MODEL", CLIP_HF_ID)
    device = _device()
    model = CLIPModel.from_pretrained(name).to(device).eval()
    processor = CLIPProcessor.from_pretrained(name)
    return NeuralClip(model, processor, device)


@lru_cache(maxsize=1)
def try_load_dinov2(weights_path: str | None = None) -> NeuralDinov2 | None:
    try:
        import torch
        from transformers import AutoImageProcessor, AutoModel
    except ImportError:
        return None
    name = weights_path or os.environ.get("SHOERAG_DINOV2_MODEL", DINOV2_HF_ID)
    device = _device()
    model = AutoModel.from_pretrained(name).to(device).eval()
    processor = AutoImageProcessor.from_pretrained(name)
    return NeuralDinov2(model, processor, device)
