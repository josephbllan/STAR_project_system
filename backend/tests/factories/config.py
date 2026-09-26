"""Runtime settings."""

from __future__ import annotations

import factory

from apps.config.models import Setting


class SettingFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Setting

    key = factory.Sequence(lambda n: f"retrieval.default_top_k.{n}")
    value = 50
    description = "Default requested result count for a new run."
    updated_by = None
