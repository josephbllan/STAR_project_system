"""Task runs.

`max_attempts` has no default on the model, deliberately: a retry policy inherited from a
default is a policy chosen for no task in particular. The factory therefore supplies one
so that tests are not obliged to care, while the model still forces a real caller to decide.
"""

from __future__ import annotations

from uuid import uuid4

import factory

from apps.tasks.models import TaskRun, TaskStatus


class TaskRunFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = TaskRun

    task_name = "datasets.scan_mount"
    # Unique globally, so every run in a test needs its own. A shared default would make the
    # second row in any test an integrity error rather than a task run.
    idempotency_key = factory.Sequence(lambda n: f"test-idempotency-key-{n}")
    status = TaskStatus.QUEUED
    max_attempts = 3
    correlation_id = factory.LazyFunction(uuid4)
    requested_by = None


class ScheduledTaskRunFactory(TaskRunFactory):
    """Beat-dispatched work, which has no requesting user. The schema permits it for exactly this
 reason and the factory exists so a test cannot forget that the case is legal."""

    task_name = "tasks.prune_task_runs"
    max_attempts = 1
    requested_by = None
