"""Scan, derive and verify. Each body is also the test entry point."""

from __future__ import annotations

from celery import shared_task
from celery.exceptions import SoftTimeLimitExceeded
from django.utils import timezone

from apps.common.storage import InvalidLocalFolderError
from apps.datasets.models import ContentObject, EvidenceFile, Mount, ScanStatus
from apps.datasets.services import (
    derive_thumbnail,
    request_corpus_encoding,
    scan_local_mount,
    verify_content,
)
from apps.tasks.inventory import INVENTORY_BY_NAME
from apps.tasks.lifecycle import (
    mark_failed,
    mark_started,
    mark_succeeded,
    set_phase,
)
from apps.tasks.models import TaskRun


def _run(task_run_id: str) -> TaskRun | None:
    return TaskRun.objects.filter(public_id=task_run_id).first()


@shared_task(name="datasets.scan_mount", bind=True)
def scan_mount(self, task_run_id: str | None = None, mount_id: int | None = None, **kwargs):
    spec = INVENTORY_BY_NAME["datasets.scan_mount"]
    self.soft_time_limit = spec.soft_time_limit
    run = _run(task_run_id) if task_run_id else None
    if run:
        run = mark_started(run)
        if run.status == "cancelled":
            return
        run = set_phase(run, "enumerating")
    try:
        mounts = (
            Mount.objects.filter(is_enabled=True).select_related("corpus")
            if mount_id is None
            else Mount.objects.filter(pk=mount_id).select_related("corpus")
        )
        if run:
            set_phase(run, "hashing")
            set_phase(run, "registering")
        for mount in mounts:
            try:
                scan_local_mount(mount, registered_by=run.requested_by if run else None)
            except InvalidLocalFolderError:
                mount.last_scan_at = timezone.now()
                mount.last_scan_status = ScanStatus.FAILED
                mount.last_scan_error_count = max(mount.last_scan_error_count, 1)
                mount.save(
                    update_fields=[
                        "last_scan_at",
                        "last_scan_status",
                        "last_scan_error_count",
                        "updated_at",
                    ]
                )
                if mount_id is not None:
                    raise
                continue
            request_corpus_encoding(mount.corpus, requested_by=run.requested_by if run else None)
        if run:
            mark_succeeded(run)
    except SoftTimeLimitExceeded as exc:
        if run:
            mark_failed(run, exc)
        raise
    except Exception as exc:
        if run:
            mark_failed(run, exc)
        raise


@shared_task(name="datasets.verify_digests")
def verify_digests(
    task_run_id: str | None = None, content_id: int | None = None, sample: bool = False, **kwargs
):
    run = _run(task_run_id) if task_run_id else None
    if run:
        run = mark_started(run)
        if run.status == "cancelled":
            return
        set_phase(run, "reading")
    qs = ContentObject.objects.all()
    if content_id:
        qs = qs.filter(pk=content_id)
    elif sample:
        qs = qs.order_by("last_verified_at")[:50]
    if run:
        set_phase(run, "comparing")
    for content in qs:
        verify_content(content, task_run=run)
    if run:
        mark_succeeded(run)


@shared_task(name="datasets.derive_artefacts")
def derive_artefacts(task_run_id: str | None = None, evidence_id: int | None = None, **kwargs):
    run = _run(task_run_id) if task_run_id else None
    if run:
        run = mark_started(run)
        if run.status == "cancelled":
            return
        set_phase(run, "decoding")
    evidence = EvidenceFile.objects.get(pk=evidence_id)
    if run:
        set_phase(run, "writing")
    derive_thumbnail(evidence)
    if run:
        mark_succeeded(run)
