"""`tasks_taskrun` as PostgreSQL holds it.

Every constraint is exercised from both sides. A constraint tested only by its violation would
pass identically if it refused every row, which is a way of being wrong that a one-sided test
cannot see.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from django.db import IntegrityError, connection, transaction
from django.db.models import ProtectedError

from apps.tasks.models import IN_FLIGHT_STATUSES, TERMINAL_STATUSES, TaskRun, TaskStatus
from tests.factories.tasks import ScheduledTaskRunFactory, TaskRunFactory

pytestmark = [pytest.mark.integration, pytest.mark.django_db]

NOW = datetime(2026, 1, 1, tzinfo=UTC)


def constraint_names() -> set[str]:
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT conname FROM pg_constraint WHERE conrelid = 'tasks_taskrun'::regclass"
        )
        return {row[0] for row in cursor.fetchall()}


def test_named_constraints_exist() -> None:
    assert {
        "uq_tasks_taskrun_idempotency_key",
        "uq_tasks_taskrun_public_id",
        "ck_tasks_taskrun_status_valid",
        "ck_tasks_taskrun_progress_non_negative",
        "ck_tasks_taskrun_terminal_finished",
    } <= constraint_names


# ----------------------------------------------------------------------------------------
# Idempotency.
# ----------------------------------------------------------------------------------------


def test_idempotency_key_is_unique() -> None:
    """This is the constraint that makes a redelivered message resolve to the existing row
 rather than to a second execution."""
    TaskRunFactory(idempotency_key="shared")
    with pytest.raises(IntegrityError), transaction.atomic():
        TaskRunFactory(idempotency_key="shared")


def test_uniqueness_is_global_and_not_per_task_name() -> None:
    """Stated as a test because the consequence is easy to get wrong in the other
 direction: the key must therefore include a scope token, or a corpus could be encoded exactly
 once in the lifetime of the installation."""
    TaskRunFactory(task_name="indexing.encode_corpus", idempotency_key="collide")
    with pytest.raises(IntegrityError), transaction.atomic():
        TaskRunFactory(task_name="search.execute_query", idempotency_key="collide")


def test_repeated_dispatch_resolves_to_the_existing_row() -> None:
    """The behaviour the dispatching service depends on: `get_or_create` on the key finds the
 first row rather than creating a second."""
    first = TaskRunFactory(idempotency_key="deterministic")
    second, created = TaskRun.objects.get_or_create(
        idempotency_key="deterministic",
        defaults={"task_name": "other", "max_attempts": 1, "correlation_id": uuid4()},
    )
    assert created is False
    assert second.pk == first.pk


# ----------------------------------------------------------------------------------------
# Status.
# ----------------------------------------------------------------------------------------


@pytest.mark.parametrize("status", [status.value for status in TaskStatus])
def test_every_declared_status_is_accepted(status: str) -> None:
    """The other half of the enumeration check. Without this, a constraint that rejected
 everything would pass the test below."""
    finished = NOW if status in {s.value for s in TERMINAL_STATUSES} else None
    assert TaskRunFactory(status=status, finished_at=finished).status == status


def test_undeclared_status_is_refused() -> None:
    """`choices` is a form-level restriction. This is the schema refusing."""
    with pytest.raises(IntegrityError), transaction.atomic():
        TaskRunFactory(status="in_progress")


# ----------------------------------------------------------------------------------------
# Progress.
# ----------------------------------------------------------------------------------------


def test_progress_may_not_be_negative() -> None:
    with pytest.raises(IntegrityError), transaction.atomic():
        TaskRunFactory(progress_current=-1)


def test_progress_may_not_exceed_its_total() -> None:
    """A counter past its total renders as a bar past its end, and more importantly means the
 `progress_current >= progress_total` completion election at has already fired."""
    with pytest.raises(IntegrityError), transaction.atomic():
        TaskRunFactory(progress_current=11, progress_total=10)


def test_progress_may_equal_its_total() -> None:
    """The boundary the completion election tests for. If this were refused, no task could ever
 record that it had finished all of its units."""
    assert TaskRunFactory(progress_current=10, progress_total=10).progress_current == 10


def test_total_may_be_unknown_while_progress_advances() -> None:
    """Null during planning, and permanently for tasks whose unit count cannot be known in
 advance; the client renders an indeterminate state rather than assuming a total."""
    run = TaskRunFactory(progress_current=7, progress_total=None)
    assert run.progress_total is None


# ----------------------------------------------------------------------------------------
# Terminal states.
# ----------------------------------------------------------------------------------------


@pytest.mark.parametrize("status", [status.value for status in TERMINAL_STATUSES])
def test_terminal_status_requires_a_finish_time(status: str) -> None:
    """A terminal run with no end time makes every duration query and every stuck-task
 alert quietly wrong."""
    with pytest.raises(IntegrityError), transaction.atomic():
        TaskRunFactory(status=status, finished_at=None)


@pytest.mark.parametrize("status", [status.value for status in IN_FLIGHT_STATUSES])
def test_in_flight_status_needs_no_finish_time(status: str) -> None:
    assert TaskRunFactory(status=status, finished_at=None).finished_at is None


def test_is_terminal_agrees_with_the_constraint() -> None:
    """The property and the check constraint must classify the same set. If they drift, code
 branches one way and the database the other."""
    for status in TaskStatus:
        terminal = status in TERMINAL_STATUSES
        run = TaskRun(status=status, finished_at=NOW if terminal else None)
        assert run.is_terminal is terminal


# ----------------------------------------------------------------------------------------
# Attribution, cancellation, retention.
# ----------------------------------------------------------------------------------------


def test_scheduled_work_has_no_requesting_user() -> None:
    """Beat dispatches with `requested_by` null, which the schema permits for exactly this
 reason."""
    assert ScheduledTaskRunFactory.requested_by is None


def test_requesting_user_cannot_be_deleted_from_under_a_run(investigator) -> None:
    """`PROTECT`, realising `ON DELETE RESTRICT`. Attribution a deletion can erase is not
 attribution."""
    TaskRunFactory(requested_by=investigator)
    with pytest.raises(ProtectedError):
        investigator.delete()


def test_cancellation_is_a_request_not_a_state() -> None:
    """Setting the request does not move the run out of flight: the task observes it
 at a phase boundary and decides, and until then the run is still running."""
    run = TaskRunFactory(status=TaskStatus.STARTED, cancel_requested_at=NOW)
    assert run.is_cancellation_requested is True
    assert run.is_terminal is False


def test_public_id_is_assigned_for_the_client_to_poll() -> None:
    assert TaskRunFactory.public_id is not None


def test_error_fields_default_to_empty_rather_than_null() -> None:
    """Empty rather than null so that a monitoring query aggregating on `error_class` needs no
 null handling, and so that the absence of an error is not indistinguishable from a field
 nobody set."""
    run = TaskRunFactory()
    assert run.error_class == ""
    assert run.error_message == ""
