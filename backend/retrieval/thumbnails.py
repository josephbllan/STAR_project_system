"""Thumbnail bytes from an inspected image. Framework-free; the caller stores the result."""

from __future__ import annotations

import io

from PIL import Image


def thumbnail(data: bytes, *, size: tuple[int, int] = (256, 256)) -> bytes:
    with Image.open(io.BytesIO(data)) as image:
        image = image.convert("RGB")
        image.thumbnail(size)
        buffer = io.BytesIO
        image.save(buffer, format="WEBP", quality=80)
        return buffer.getvalue
