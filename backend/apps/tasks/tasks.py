from __future__ import annotations

from datetime import timedelta

from celery import shared_task
from django.conf import settings
from django.utils import timezone

from apps.tasks.lifecycle import mark_started, mark_succeeded, set_phase
from apps.tasks.models import TERMINAL_STATUSES, TaskRun


@shared_task(name="tasks.prune_task_runs")
def prune_task_runs(task_run_id: str | None = None, **kwargs):
    run = TaskRun.objects.filter(public_id=task_run_id).first() if task_run_id else None
    if run:
        run = mark_started(run)
        if run.status == "cancelled":
            return
        set_phase(run, "deleting")
    days = getattr(settings, "TASK_RUN_RETENTION_DAYS", 30)
    cutoff = timezone.now() - timedelta(days=days)
    # PROTECT FKs refuse deletion of a run still referenced by evidential rows.
    TaskRun.objects.filter(
        status__in=[s.value for s in TERMINAL_STATUSES],
        finished_at__lt=cutoff,
    ).exclude(pk=run.pk if run else None).delete()
    if run:
        mark_succeeded(run)
