"""Abstract bases carrying the columns that puts on every table."""

import uuid

from django.db import models
from django.db.models import Func


class TimeStampedModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class PublicIdModel(models.Model):
    """Opaque external identifier. Version 4, never version 7: a sortable identifier would
 leak the creation order of evidential records."""

    public_id = models.UUIDField(
        default=uuid.uuid4,
        db_default=Func(function="gen_random_uuid"),
        editable=False,
    )

    class Meta:
        abstract = True
