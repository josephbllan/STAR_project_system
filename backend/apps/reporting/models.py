"""Reporting and export.

Two decisions here are security controls rather than conveniences.

**The row is created when the export is requested, not when it completes**. A request that
fails is a record of an attempted export, and the threat model requires attempts to be visible - exfiltration by repeated failing export is exactly the pattern a completion-only record
would hide.

**`ix_reporting_report_requested_by_time` is the principal detection control**.
It exists to answer "how much has this person exported, over what period", which is the query the
monitoring runs. It is an index whose justification is a threat and not a page load.

`DELETE` is revoked on this table. The file expires and is removed; the record that an
export occurred outlives it. A report row with `status = 'expired'` and a null storage key is the
normal end state, not a broken one.
"""

from __future__ import annotations

from django.conf import settings
from django.db import models
from django.db.models import Q

from apps.common.models import PublicIdModel, TimeStampedModel
from apps.common.querysets import ScopedManager
from apps.datasets.models import SHA256_LENGTH, SHA256_PATTERN


class ReportFormat(models.TextChoices):
    """Only `pdf` is produced in release 1. The other two are in the enumeration because the column
 is `varchar(8)` either way and adding a value to a check constraint later is a migration; the
 serializer is what restricts the set a client may ask for."""

    PDF = "pdf", "PDF"
    CSV = "csv", "CSV"
    JSON = "json", "JSON"


class ReportStatus(models.TextChoices):
    REQUESTED = "requested", "Requested"
    GENERATING = "generating", "Generating"
    AVAILABLE = "available", "Available"
    FAILED = "failed", "Failed"
    EXPIRED = "expired", "Expired"


class Report(TimeStampedModel, PublicIdModel):
    case = models.ForeignKey(
        "cases.Case", on_delete=models.PROTECT, related_name="reports", db_index=False
    )
    format = models.CharField(max_length=8, choices=ReportFormat.choices)
    #: Which runs, queries and results are included. Genuinely variable between export kinds, which
    #: is what JSON is for; the serializer validates it per format.
    scope = models.JSONField(default=dict)
    status = models.CharField(
        max_length=16, choices=ReportStatus.choices, default=ReportStatus.REQUESTED
    )

    # DJ001 on the three below: null means "no file was ever produced", which is distinct from a
    # file that was produced and has since expired - that keeps its digest and loses only its key.
    storage_key = models.CharField(max_length=512, null=True, blank=True)  # noqa: DJ001
    #: The digest of the produced file, so an exported report is itself verifiable. Without it, a
    #: report presented later cannot be shown to be the one the system generated.
    sha256 = models.CharField(max_length=SHA256_LENGTH, null=True, blank=True)  # noqa: DJ001
    byte_size = models.BigIntegerField(null=True, blank=True)

    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="requested_reports",
        db_index=False,
    )
    task_run = models.ForeignKey(
        "tasks.TaskRun",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="reports",
    )
    available_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    #: Convenience only. The audit trail remains authoritative on who downloaded what and when,
    #: because this counter can be incremented without saying by whom.
    download_count = models.IntegerField(default=0)
    #: Redacted. A class of failure, never a path, a query or evidential content.
    failure_reason = models.CharField(max_length=200, default="", blank=True)

    objects = ScopedManager(case_paths=("case",))

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["public_id"], name="uq_reporting_report_public_id"),
            models.UniqueConstraint(fields=["storage_key"], name="uq_reporting_report_storage_key"),
            models.CheckConstraint(
                condition=Q(format__in=[f.value for f in ReportFormat]),
                name="ck_reporting_report_format_valid",
            ),
            models.CheckConstraint(
                condition=Q(status__in=[s.value for s in ReportStatus]),
                name="ck_reporting_report_status_valid",
            ),
            # A report offered for download with no object behind it is a 500 waiting to
            # happen, and worse, a record asserting an export that did not produce a file.
            models.CheckConstraint(
                condition=~Q(status=ReportStatus.AVAILABLE)
                | Q(storage_key__isnull=False, sha256__isnull=False),
                name="ck_reporting_report_available_has_object",
            ),
            # The digest column needs the same character-class check as every other digest
            # in the schema: a report's digest is what makes an exported file verifiable.
            models.CheckConstraint(
                condition=Q(sha256__isnull=True) | Q(sha256__regex=SHA256_PATTERN),
                name="ck_reporting_report_sha256_hex",
            ),
        ]
        indexes = [
            # The principal detection control: volume of export per user per period.
            # Leading column matches the filter, trailing column the ordering.
            models.Index(
                fields=["requested_by", "-created_at"],
                name="ix_reporting_report_requested_by_time",
            ),
            models.Index(
                fields=["expires_at"],
                condition=Q(status=ReportStatus.AVAILABLE),
                name="ix_reporting_report_expiry",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.format} report {self.public_id}"

    @property
    def has_object(self) -> bool:
        return self.storage_key is not None
