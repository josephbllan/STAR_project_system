"""The closed task inventory.

A task that is not a row here does not exist. A test enumerates the Celery registry and asserts
equality with this table, so a task introduced without being specified fails the build rather than
quietly joining the worker.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

QUEUE_DEFAULT: Final = "default"
QUEUE_ENCODING: Final = "encoding"
QUEUE_REPORTING: Final = "reporting"
QUEUE_MAINTENANCE: Final = "maintenance"

QUEUES: Final = (QUEUE_DEFAULT, QUEUE_ENCODING, QUEUE_REPORTING, QUEUE_MAINTENANCE)


@dataclass(frozen=True, slots=True)
class TaskSpec:
    name: str
    queue: str
    soft_time_limit: int
    max_attempts: int
    phases: tuple[str,...]

    @property
    def time_limit(self) -> int:
        """Hard limit: the soft limit plus sixty seconds."""
        return self.soft_time_limit + 60


INVENTORY: Final[tuple[TaskSpec,...]] = (
    TaskSpec(
        "datasets.scan_mount", QUEUE_MAINTENANCE, 900, 3, ("enumerating", "hashing", "registering")
    ),
    TaskSpec("datasets.verify_digests", QUEUE_MAINTENANCE, 900, 2, ("reading", "comparing")),
    TaskSpec("datasets.derive_artefacts", QUEUE_DEFAULT, 300, 3, ("decoding", "writing")),
    TaskSpec(
        "indexing.encode_batch", QUEUE_ENCODING, 600, 3, ("loading", "encoding", "persisting")
    ),
    TaskSpec("indexing.encode_corpus", QUEUE_DEFAULT, 120, 1, ("planning", "dispatching")),
    TaskSpec(
        "search.build_ann_index", QUEUE_MAINTENANCE, 3600, 1, ("building", "measuring_recall")
    ),
    TaskSpec(
        "search.execute_query",
        QUEUE_DEFAULT,
        300,
        2,
        ("encoding", "routing", "searching", "fusing", "persisting"),
    ),
    TaskSpec(
        "reporting.render_report", QUEUE_REPORTING, 600, 2, ("collecting", "rendering", "hashing")
    ),
    TaskSpec("reporting.expire_reports", QUEUE_MAINTENANCE, 300, 1, ("selecting", "deleting")),
    TaskSpec("tasks.prune_task_runs", QUEUE_MAINTENANCE, 300, 1, ("deleting",)),
)

INVENTORY_BY_NAME: Final[dict[str, TaskSpec]] = {spec.name: spec for spec in INVENTORY}

#: Longest hard limit plus a margin, so a message cannot be redelivered while its worker is
#: still inside the hard limit. The margin is ten minutes: enough for process
#: teardown, not enough to hide a newly added hour-long task.
VISIBILITY_TIMEOUT_MARGIN_SECONDS: Final = 600
VISIBILITY_TIMEOUT_SECONDS: Final = (
    max(spec.time_limit for spec in INVENTORY) + VISIBILITY_TIMEOUT_MARGIN_SECONDS
)

TASK_ROUTES: Final[dict[str, dict[str, str]]] = {
    spec.name: {"queue": spec.queue} for spec in INVENTORY
}

PERMITTED_PHASES: Final[frozenset[str]] = frozenset(
    phase for spec in INVENTORY for phase in spec.phases
)
