"""Celery is imported lazily so Django can finish loading settings first.

`celery -A config` still resolves `config.celery:app`. Importing the worker app
from this package during `django.setup` waits on settings that are already
being imported.
"""

from __future__ import annotations

from typing import Any

__all__ = ("celery_app",)


def __getattr__(name: str) -> Any:
    if name == "celery_app":
        from config.celery import app as celery_app

        return celery_app
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
