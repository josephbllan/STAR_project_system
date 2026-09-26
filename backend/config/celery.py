"""The Celery application: four queues, no result backend, beat for scheduled work.

`CELERY_RESULT_BACKEND` is unset on purpose. `chord`, `chain` with result access and
`AsyncResult.get` are therefore unavailable, and progress is aggregated in PostgreSQL.
"""

from __future__ import annotations

import os

from celery import Celery
from celery.schedules import crontab

from config.dotenv import load_local_env

load_local_env()
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.local")

app = Celery("shoerag")
app.config_from_object("django.conf:settings", namespace="CELERY")
# Packages, not Django's app registry: this module is imported from `config/__init__`
# while settings are still loading, and asking the registry then never returns.
app.autodiscover_tasks(
    lambda: [
        "apps.datasets",
        "apps.search",
        "apps.reporting",
        "apps.indexing",
        "apps.tasks",
    ]
)

app.conf.beat_schedule = {
    "reporting.expire_reports": {
        "task": "reporting.expire_reports",
        "schedule": crontab(minute=0),
    },
    "tasks.prune_task_runs": {
        "task": "tasks.prune_task_runs",
        "schedule": crontab(hour=3, minute=15),
    },
    "datasets.verify_digests": {
        "task": "datasets.verify_digests",
        "schedule": crontab(hour=4, minute=0),
        "kwargs": {"sample": True},
    },
    "datasets.scan_mount": {
        "task": "datasets.scan_mount",
        "schedule": crontab(minute="*/30"),
        "kwargs": {"mount_id": None},
    },
}
