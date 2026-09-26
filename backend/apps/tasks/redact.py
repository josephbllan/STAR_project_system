"""Strip filesystem paths, credentials and evidential content from a failure summary."""

from __future__ import annotations

import re

_PATH = re.compile(r"(?:[A-Za-z]:)?(?:[/\\][\w.+-]+)+")
_HEX = re.compile(r"\b[0-9a-f]{64}\b", re.IGNORECASE)


def redact(message: str, *, limit: int = 500) -> str:
    """Return a summary safe to store on `TaskRun.error_message` and show to any caller."""
    cleaned = _PATH.sub("[path]", message)
    cleaned = _HEX.sub("[digest]", cleaned)
    cleaned = cleaned.replace("\n", " ").strip()
    if len(cleaned) > limit:
        return cleaned[: limit - 1] + "…"
    return cleaned
