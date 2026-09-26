"""Server-side sanitisation before storage. Never left to the renderer."""

from __future__ import annotations

import re

_TAGS = re.compile(r"<[^>]+>")
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


def sanitise(text: str) -> str:
    cleaned = _TAGS.sub("", text)
    cleaned = _CONTROL.sub("", cleaned)
    return cleaned.strip()
