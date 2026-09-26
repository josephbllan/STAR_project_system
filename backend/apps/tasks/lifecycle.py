"""State transitions for `TaskRun`.

The worker never writes status by assignment. Every transition goes through one of these
functions so that `finished_at` is set with a terminal status, so that a cancellation
request is observed rather than raced past, and so that progress is incremented with `F` rather
than read-modify-written.
"""

from __future__ import annotations

from django.db.models import F
from django.db.models.functions import Coalesce, Least
from django.utils import timezone

from apps.tasks.models import TERMINAL_STATUSES, TaskRun, TaskStatus
from apps.tasks.redact import redact


def mark_started(run: TaskRun) -> TaskRun:
    """The first instruction of every task. Honours a cancellation already waiting."""
    run.refresh_from_db()
    if run.cancel_requested_at is not None:
        return mark_cancelled(run)
    if run.status in {status.value for status in TERMINAL_STATUSES}:
        return run
    now = timezone.now()
    TaskRun.objects.filter(pk=run.pk, status__in=[TaskStatus.QUEUED, TaskStatus.RETRYING]).update(
        status=TaskStatus.STARTED,
        started_at=now,
        attempts=F("attempts") + 1,
    )
    run.refresh_from_db()
    return run


def set_phase(run: TaskRun, phase: str) -> TaskRun:
    """A phase boundary is also a cancellation observation point."""
    run.refresh_from_db()
    if run.cancel_requested_at is not None:
        return mark_cancelled(run)
    TaskRun.objects.filter(pk=run.pk).update(phase=phase)
    run.refresh_from_db()
    return run


def mark_succeeded(run: TaskRun) -> TaskRun:
    now = timezone.now()
    TaskRun.objects.filter(pk=run.pk).exclude(
        status__in=[status.value for status in TERMINAL_STATUSES]
    ).update(status=TaskStatus.SUCCEEDED, finished_at=now)
    run.refresh_from_db()
    return run


def mark_failed(run: TaskRun, exc: BaseException) -> TaskRun:
    now = timezone.now()
    TaskRun.objects.filter(pk=run.pk).exclude(
        status__in=[status.value for status in TERMINAL_STATUSES]
    ).update(
        status=TaskStatus.FAILED,
        finished_at=now,
        error_class=type(exc).__name__,
        error_message=redact(str(exc)),
    )
    run.refresh_from_db()
    return run


def mark_cancelled(run: TaskRun) -> TaskRun:
    now = timezone.now()
    TaskRun.objects.filter(pk=run.pk).exclude(
        status__in=[status.value for status in TERMINAL_STATUSES]
    ).update(status=TaskStatus.CANCELLED, finished_at=now)
    run.refresh_from_db()
    return run


def request_cancellation(run: TaskRun) -> TaskRun:
    """Records a request. Does not assert that work has stopped."""
    if run.cancel_requested_at is None:
        TaskRun.objects.filter(pk=run.pk, cancel_requested_at__isnull=True).update(
            cancel_requested_at=timezone.now()
        )
        run.refresh_from_db()
    return run


def increment_progress(run: TaskRun, by: int) -> TaskRun:
    """`F` so two batches completing together cannot lose a count."""
    if by <= 0:
        run.refresh_from_db()
        return run
    TaskRun.objects.filter(pk=run.pk).update(
        progress_current=Least(
            F("progress_current") + by,
            Coalesce(F("progress_total"), F("progress_current") + by),
        )
    )
    run.refresh_from_db()
    return run


def elect_parent_completion(parent: TaskRun) -> bool:
    """Affected-row count elects exactly one finisher."""
    now = timezone.now()
    updated = (
        TaskRun.objects.filter(
            pk=parent.pk,
            progress_total__isnull=False,
            progress_current__gte=F("progress_total"),
        )
.exclude(status__in=[status.value for status in TERMINAL_STATUSES])
.update(status=TaskStatus.SUCCEEDED, finished_at=now)
    )
    parent.refresh_from_db()
    return updated == 1
