"""Runtime settings that an administrator may change without a deployment.

**No credential, key or connection string is stored here**. Secrets are delivered at
runtime through the environment and are never persisted by the application. This is stated in the
table's own docstring rather than only in the requirements because the table is the obvious place to
put one, and the obvious place is where it will be put unless the prohibition is visible here.

`value` is JSON because the variability is genuine: a fusion default is a number, a permitted
classification list is a list, an expiry window is a number of seconds. What each key means
and what shape its value takes is stated at the point the key is read, not in a schema this table
cannot enforce.
"""

from __future__ import annotations

from django.conf import settings
from django.db import models

from apps.common.models import TimeStampedModel


class Setting(TimeStampedModel):
    key = models.CharField(max_length=100)
    value = models.JSONField()
    description = models.TextField(default="", blank=True)
    #: Null where a setting was seeded by a migration rather than changed by a person. Every change
    #: made through the API also produces an `admin.setting.changed` audit event, which is where
    #: the history lives; this column answers only "who last touched it".
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="updated_settings",
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["key"], name="uq_config_setting_key"),
        ]

    def __str__(self) -> str:
        return self.key
