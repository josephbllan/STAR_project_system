"""Loads `.env` for local development and local test runs.

Deliberately not a dependency and deliberately not used by `wsgi`/`asgi`: outside local
development the variables arrive from the process environment, and a server that would
silently fall back to a file on disk is a server that can start with the wrong credentials.
Existing variables always win, so CI is unaffected.
"""

import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def load_local_env(path: Path | None = None) -> None:
    env_file = path or PROJECT_ROOT / ".env"
    if not env_file.is_file():
        return
    for raw_line in env_file.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, _, value = line.partition("=")
        os.environ.setdefault(name.strip(), value.strip().strip("'\""))
