"""Create a task run and enqueue it after the surrounding transaction commits.

The row is created first. A worker that starts before commit therefore finds nothing and the
message is retried. The opposite order - enqueue then
insert - is how a worker observes a row that does not exist yet and fails on a fact it cannot
recover from.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
from typing import Any
from uuid import UUID, uuid4

from django.db import connection, transaction
from django.utils import timezone
from django_guid import get_guid

from apps.tasks.inventory import INVENTORY_BY_NAME
from apps.tasks.models import IN_FLIGHT_STATUSES, TaskRun, TaskStatus


class UnknownTaskError(ValueError):
    """A name that is not in the inventory. Refused rather than dispatched."""


def derive_idempotency_key(
    task_name: str,
    params: dict[str, Any],
    scope_token: str,
    *,
    client_key: str | None = None,
) -> str:
    """SHA-256 of task name + canonical params + scope token, or of the client header."""
    if client_key:
        payload = f"{task_name}\0{client_key}"
    else:
        canonical = json.dumps(params, sort_keys=True, separators=(",", ":"), default=str)
        payload = f"{task_name}\0{canonical}\0{scope_token}"
    return hashlib.sha256(payload.encode()).hexdigest()


def _advisory_key(task_name: str, params: dict[str, Any]) -> int:
    digest = hashlib.sha256(
        f"{task_name}:{json.dumps(params, sort_keys=True, default=str)}".encode()
    ).digest()
    return int.from_bytes(digest[:8], "big") % (2**63)


def find_in_flight(
    task_name: str,
    params: dict[str, Any],
    *,
    target_public_id: UUID | None = None,
) -> TaskRun | None:
    """Duplicated submission: an equivalent non-terminal run already exists."""
    # Dedup is by task + target. Without a target, matching every in-flight row of the same
    # name collapses distinct work (encode batches) into the first queued child.
    target = target_public_id or params.get("target_public_id")
    if not target:
        return None
    qs = TaskRun.objects.filter(
        task_name=task_name, status__in=[s.value for s in IN_FLIGHT_STATUSES]
    )
    with contextlib.suppress(ValueError, TypeError, AttributeError):
        qs = qs.filter(target_public_id=UUID(str(target)))
    return qs.order_by("queued_at").first()


def dispatch(
    task_name: str,
    params: dict[str, Any],
    *,
    scope_token: str,
    requested_by=None,
    target_type: str = "",
    target_public_id: UUID | None = None,
    client_key: str | None = None,
    correlation_id: UUID | None = None,
) -> tuple[TaskRun, bool]:
    """Return `(run, created)`. `created` is False when an equivalent in-flight run is reused.

 The advisory lock serialises two simultaneous submissions that would otherwise both
 find nothing and both insert. It is transaction-scoped so a crashed connection cannot hold it.
 """
    spec = INVENTORY_BY_NAME.get(task_name)
    if spec is None:
        raise UnknownTaskError(task_name)

    if correlation_id is None:
        guid = get_guid()
        correlation_id = UUID(guid) if guid else uuid4()

    key = derive_idempotency_key(task_name, params, scope_token, client_key=client_key)

    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_advisory_xact_lock(%s)", [_advisory_key(task_name, params)])

    existing = find_in_flight(task_name, params, target_public_id=target_public_id)
    if existing is not None:
        return existing, False

    run, created = TaskRun.objects.get_or_create(
        idempotency_key=key,
        defaults={
            "task_name": task_name,
            "status": TaskStatus.QUEUED,
            "max_attempts": spec.max_attempts,
            "correlation_id": correlation_id,
            "requested_by": requested_by,
            "target_type": target_type,
            "target_public_id": target_public_id,
            "queued_at": timezone.now(),
        },
    )
    if not created:
        if run.status in {TaskStatus.FAILED, TaskStatus.CANCELLED}:
            TaskRun.objects.filter(pk=run.pk).update(
                status=TaskStatus.QUEUED,
                finished_at=None,
                error_class="",
                error_message="",
                celery_task_id=None,
                phase="",
                cancel_requested_at=None,
            )
            run.refresh_from_db()
        else:
            return run, False

    payload = {**params, "task_run_id": str(run.public_id)}
    transaction.on_commit(lambda: _enqueue(task_name, payload, run.pk))
    return run, True


def _enqueue(task_name: str, params: dict[str, Any], run_pk: int) -> None:
    from config.celery import app

    result = app.send_task(task_name, kwargs=params)
    TaskRun.objects.filter(pk=run_pk, celery_task_id__isnull=True).update(celery_task_id=result.id)


def requeue(run: TaskRun, params: dict[str, Any]) -> None:
    """Enqueue an existing in-flight row again (orphan parent, worker lost the message)."""
    payload = {**params, "task_run_id": str(run.public_id)}
    transaction.on_commit(lambda: _enqueue(run.task_name, payload, run.pk))
