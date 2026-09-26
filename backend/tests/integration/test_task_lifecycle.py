from unittest.mock import patch
from uuid import uuid4

import pytest
from django.db import transaction

from apps.tasks.dispatch import derive_idempotency_key, dispatch
from apps.tasks.lifecycle import (
    elect_parent_completion,
    increment_progress,
    mark_failed,
    mark_started,
    request_cancellation,
)
from apps.tasks.models import TaskRun, TaskStatus
from apps.tasks.redact import redact
from tests.factories.tasks import TaskRunFactory

pytestmark = [pytest.mark.django_db]


def test_derive_key_includes_the_scope_token() -> None:
    first = derive_idempotency_key("indexing.encode_corpus", {"corpus_id": 1}, "scope-a")
    second = derive_idempotency_key("indexing.encode_corpus", {"corpus_id": 1}, "scope-b")
    assert first != second


def test_dispatch_is_idempotent_on_the_derived_key() -> None:
    token = str(uuid4())
    first, created = dispatch(
        "tasks.prune_task_runs",
        {"target_public_id": token},
        scope_token=token,
    )
    assert created is True
    second, created_again = dispatch(
        "tasks.prune_task_runs",
        {"target_public_id": token},
        scope_token=token,
    )
    assert created_again is False
    assert first.pk == second.pk


def test_mark_started_honours_a_waiting_cancellation() -> None:
    run = TaskRunFactory()
    request_cancellation(run)
    run = mark_started(run)
    assert run.status == TaskStatus.CANCELLED
    assert run.finished_at is not None


def test_a_path_in_a_failure_is_redacted() -> None:
    run = TaskRunFactory()
    mark_started(run)
    mark_failed(run, RuntimeError("could not read D:\\evidence\\secret.jpg"))
    run.refresh_from_db()
    assert "secret.jpg" not in run.error_message
    assert "[path]" in run.error_message
    assert run.error_class == "RuntimeError"


def test_redact_strips_a_digest() -> None:
    digest = "a" * 64
    assert digest not in redact(f"observed {digest}")


def test_progress_aggregation_elects_one_finisher() -> None:
    parent = TaskRunFactory()
    TaskRun.objects.filter(pk=parent.pk).update(progress_total=10, progress_current=0)
    increment_progress(parent, 6)
    assert elect_parent_completion(parent) is False
    increment_progress(parent, 4)
    assert elect_parent_completion(parent) is True
    assert elect_parent_completion(parent) is False
    parent.refresh_from_db()
    assert parent.status == TaskStatus.SUCCEEDED


@pytest.mark.django_db(transaction=True)
def test_dispatch_requeues_a_failed_run_with_the_same_key() -> None:
    token = str(uuid4())
    with patch("apps.tasks.dispatch._enqueue"):
        run, created = dispatch(
            "tasks.prune_task_runs",
            {"target_public_id": token},
            scope_token=token,
        )
    mark_started(run)
    mark_failed(run, RuntimeError("worker lost"))
    with patch("apps.tasks.dispatch._enqueue") as enqueue:
        again, created_again = dispatch(
            "tasks.prune_task_runs",
            {"target_public_id": token},
            scope_token=token,
        )
    assert created is True
    assert created_again is True
    assert again.pk == run.pk
    assert again.status == TaskStatus.QUEUED
    assert enqueue.called


def test_encode_batches_are_not_collapsed_into_the_first_in_flight_run() -> None:
    """Each batch is distinct work. Dedup without a target used to reuse the first queued
    encode_batch and leave the parent stuck at 32 / N."""
    with patch("apps.tasks.dispatch._enqueue"):
        first, created = dispatch(
            "indexing.encode_batch",
            {"content_ids": [1, 2], "encoder_id": 1, "parent_task_run_id": "parent"},
            scope_token="1:1:1",
        )
        second, created_again = dispatch(
            "indexing.encode_batch",
            {"content_ids": [3, 4], "encoder_id": 1, "parent_task_run_id": "parent"},
            scope_token="1:1:3",
        )
    assert created is True
    assert created_again is True
    assert first.pk != second.pk
    assert TaskRun.objects.filter(task_name="indexing.encode_batch").count() == 2


def test_on_commit_is_empty_before_commit() -> None:
    """The enqueue is registered as an on-commit callback, not sent inside the
 transaction."""
    with transaction.atomic():
        callbacks = transaction.get_connection.run_on_commit
        before = len(callbacks)
        with patch("apps.tasks.dispatch._enqueue"):
            dispatch(
                "reporting.expire_reports",
                {"target_public_id": str(uuid4())},
                scope_token=str(uuid4()),
            )
        after = len(transaction.get_connection.run_on_commit)
        assert after > before
