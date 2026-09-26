"""Image bounds applied *before* a decode that could exhaust memory."""

from __future__ import annotations

import io
from dataclasses import dataclass

from django.conf import settings
from PIL import Image, ImageFile, UnidentifiedImageError

# Refuse a truncated stream rather than inventing pixels for the missing end.
ImageFile.LOAD_TRUNCATED_IMAGES = False
Image.MAX_IMAGE_PIXELS = None  # we apply our own bound, below


class ImageRejectedError(ValueError):
    """The bytes are not an image this system will store."""


@dataclass(frozen=True, slots=True)
class InspectedImage:
    media_type: str
    width: int
    height: int
    pixel_count: int


_MEDIA_TYPES = {
    "JPEG": "image/jpeg",
    "PNG": "image/png",
    "WEBP": "image/webp",
    "TIFF": "image/tiff",
    "BMP": "image/bmp",
}


def inspect_image(data: bytes) -> InspectedImage:
    """Open the header, read declared dimensions, then refuse if they exceed the bound.

 Dimensions are taken from the header so a compressed file claiming thirty-thousand-plus
 pixels on a side is refused without being decompressed.
 """
    if not data:
        raise ImageRejectedError("The file holds no bytes.")
    max_bytes = getattr(settings, "MAX_UPLOAD_BYTES", 50 * 1024 * 1024)
    if len(data) > max_bytes:
        raise ImageRejectedError("The file exceeds the configured upload limit.")
    try:
        with Image.open(io.BytesIO(data)) as image:
            width, height = image.size
            fmt = image.format
    except (UnidentifiedImageError, OSError) as exc:
        raise ImageRejectedError("The file is not a recognised image.") from exc
    if width < 1 or height < 1:
        raise ImageRejectedError("The image has no pixels.")
    max_pixels = getattr(settings, "MAX_IMAGE_PIXELS", 30_000 * 30_000)
    if width * height > max_pixels or width > 30_000 or height > 30_000:
        raise ImageRejectedError(
            "The image's pixel dimensions exceed the limit this system will decode."
        )
    media = _MEDIA_TYPES.get(fmt or "", "application/octet-stream")
    if media == "application/octet-stream":
        raise ImageRejectedError("That image format is not accepted.")
    return InspectedImage(media_type=media, width=width, height=height, pixel_count=width * height)
