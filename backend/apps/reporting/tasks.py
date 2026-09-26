from __future__ import annotations

from celery import shared_task

from apps.reporting.models import Report
from apps.reporting.services import complete_report, expire_due_reports
from apps.tasks.lifecycle import mark_failed, mark_started, mark_succeeded, set_phase
from apps.tasks.models import TaskRun


def _run(task_run_id: str | None) -> TaskRun | None:
    if not task_run_id:
        return None
    return TaskRun.objects.filter(public_id=task_run_id).first()


@shared_task(name="reporting.render_report")
def render_report(task_run_id: str | None = None, report_id: int | None = None, **kwargs):
    run = _run(task_run_id)
    if run:
        run = mark_started(run)
        if run.status == "cancelled":
            return
        set_phase(run, "collecting")
    report = Report.objects.select_related("case", "requested_by").get(pk=report_id)
    try:
        if run:
            set_phase(run, "rendering")
        complete_report(report)
        if run:
            set_phase(run, "hashing")
            mark_succeeded(run)
    except Exception as exc:
        if run:
            mark_failed(run, exc)
        raise


@shared_task(name="reporting.expire_reports")
def expire_reports(task_run_id: str | None = None, **kwargs):
    run = _run(task_run_id)
    if run:
        run = mark_started(run)
        if run.status == "cancelled":
            return
        set_phase(run, "selecting")
    expire_due_reports
    if run:
        set_phase(run, "deleting")
        mark_succeeded(run)
