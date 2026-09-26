"""`TaskRun` - the authoritative record of every asynchronous operation.

There is no Celery result backend. That is a deliberate decision with a
consequence: this table is the only store of task state, so there is exactly one place to look
and no possibility of two stores disagreeing. It also means the aggregation patterns that depend
on reading results - `chord`, `chain` with result access - are unavailable, and progress is
aggregated here instead.

This model is created before the tasks that write to it, because it is referenced by mount,
artefact, verification, embedding, run and query, and because those references are `PROTECT`:
a task run cannot be pruned while an evidential record still points at it.
"""

from __future__ import annotations

from django.conf import settings
from django.db import models
from django.db.models import Q

from apps.common.models import PublicIdModel, TimeStampedModel


class TaskStatus(models.TextChoices):
    """The closed set of states. `retrying` is distinct from `started` because a run that has
 failed twice and is waiting to try again is operationally different from one making
 progress, and the stuck-task alert needs to tell them apart."""

    QUEUED = "queued", "Queued"
    STARTED = "started", "Started"
    RETRYING = "retrying", "Retrying"
    SUCCEEDED = "succeeded", "Succeeded"
    FAILED = "failed", "Failed"
    CANCELLED = "cancelled", "Cancelled"


#: Reaching one of these means the run is over. `ck_tasks_taskrun_terminal_finished` requires
#: `finished_at` alongside, so a terminal run without an end time cannot be recorded.
TERMINAL_STATUSES = (TaskStatus.SUCCEEDED, TaskStatus.FAILED, TaskStatus.CANCELLED)

#: Work in flight. The partial index and the operational view both use exactly this set.
IN_FLIGHT_STATUSES = (TaskStatus.QUEUED, TaskStatus.STARTED, TaskStatus.RETRYING)


class TaskRun(TimeStampedModel, PublicIdModel):
    task_name = models.CharField(max_length=200)

    #: Correlates to broker diagnostics. Nullable because the row is created inside the
    #: dispatching request's transaction, before `apply_async` has returned an identifier -
    #: which is the correct order, since a row created after dispatch could be missed by a
    #: worker that started first.
    # DJ001 warns against `null=True` on a text field because it creates two empty states. Here
    # there is only one: an empty string is never written, and null means "not yet dispatched",
    # which is a fact the operational views need to distinguish from "dispatched" ( 9).
    celery_task_id = models.CharField(max_length=64, null=True, blank=True)  # noqa: DJ001

    #: Unique globally, not partially. The consequence is that the key cannot be derived from
    #: the task name and parameters alone: that would permanently prevent a second legitimate
    #: execution, so a corpus could be encoded exactly once in the lifetime of the installation.
    #: A scope token - the `public_id` of the intent row the operation serves - makes each
    #: deliberate request distinct while leaving every redelivery of one message identical
    #:.
    idempotency_key = models.CharField(max_length=200)

    status = models.CharField(max_length=16, choices=TaskStatus.choices, default=TaskStatus.QUEUED)

    #: Named phase of a long job, so that a user watching a two-hour
    #: encode sees which of three things is happening rather than a bar. Also gives cancellation
    #: its defined observation points.
    phase = models.CharField(max_length=50, default="", blank=True)

    progress_current = models.IntegerField(default=0)
    #: Null while unknown - during planning, and permanently for tasks whose unit count cannot
    #: be known in advance. The client renders an indeterminate state rather than assuming a
    #: total.
    progress_total = models.IntegerField(null=True, blank=True)

    attempts = models.IntegerField(default=0)
    #: Per task and never inherited from a default, because a retry policy that applies to
    #: every task is a retry policy chosen for none of them.
    max_attempts = models.IntegerField()

    queued_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    #: A request, not an instruction. The task observes it at each phase boundary and between
    #: batches; nothing is signalled and nothing is rolled back, because a signal cannot
    #: checkpoint.
    cancel_requested_at = models.DateTimeField(null=True, blank=True)

    #: Null for scheduled work, which the beat schedule relies on. `PROTECT` because a
    #: user who dispatched work that produced evidence cannot be deleted out from under it.
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="task_runs",
    )

    #: Propagated from the request boundary, so that one user's report of a failure resolves to
    #: the request and to the asynchronous work it dispatched.
    correlation_id = models.UUIDField()

    #: What the work was about, held as a loose pair rather than a generic foreign key: the
    #: targets span six apps, and a `GenericForeignKey` would put an unindexed content-type
    #: join into the operational views that read this table most.
    target_type = models.CharField(max_length=50, default="", blank=True)
    target_public_id = models.UUIDField(null=True, blank=True)

    #: The exception class, recorded separately because that is what monitoring aggregates on.
    error_class = models.CharField(max_length=200, default="", blank=True)
    #: A redacted summary. Never a filesystem path, evidential content or a credential - this
    #: field is read by anyone who can see the task run.
    error_message = models.CharField(max_length=500, default="", blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["idempotency_key"], name="uq_tasks_taskrun_idempotency_key"
            ),
            models.UniqueConstraint(fields=["public_id"], name="uq_tasks_taskrun_public_id"),
            models.CheckConstraint(
                condition=Q(status__in=[choice.value for choice in TaskStatus]),
                name="ck_tasks_taskrun_status_valid",
            ),
            models.CheckConstraint(
                condition=Q(progress_current__gte=0)
                & (
                    Q(progress_total__isnull=True)
                    | Q(progress_total__gte=models.F("progress_current"))
                ),
                name="ck_tasks_taskrun_progress_non_negative",
            ),
            # A terminal run without an end time would make every duration query and
            # every stuck-task alert quietly wrong.
            models.CheckConstraint(
                condition=~Q(status__in=[status.value for status in TERMINAL_STATUSES])
                | Q(finished_at__isnull=False),
                name="ck_tasks_taskrun_terminal_finished",
            ),
        ]
        indexes = [
            # Partial, because the operational view and the stuck-task alert only ever ask about
            # work in flight, and that set stays small while the table grows without bound.
            models.Index(
                fields=["status", "queued_at"],
                condition=Q(status__in=[status.value for status in IN_FLIGHT_STATUSES]),
                name="ix_tasks_taskrun_status_queued",
            ),
            models.Index(fields=["correlation_id"], name="ix_tasks_taskrun_correlation"),
            models.Index(fields=["celery_task_id"], name="ix_tasks_taskrun_celery_task_id"),
            models.Index(
                fields=["requested_by", "-queued_at"], name="ix_tasks_taskrun_requested_by_time"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.task_name} ({self.status})"

    @property
    def is_terminal(self) -> bool:
        return self.status in {status.value for status in TERMINAL_STATUSES}

    @property
    def is_cancellation_requested(self) -> bool:
        """Read by the task at each phase boundary. Deliberately a plain column read: one
 indexed read of its own row is cheap enough per batch and far too expensive per
 image."""
        return self.cancel_requested_at is not None
