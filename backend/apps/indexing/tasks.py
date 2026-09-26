from __future__ import annotations

from celery import shared_task

from apps.datasets.models import ContentObject, Corpus
from apps.indexing.services import content_needing_encoding, encode_content, plan_batches
from apps.search.models import Encoder
from apps.tasks.dispatch import dispatch
from apps.tasks.lifecycle import (
    elect_parent_completion,
    increment_progress,
    mark_cancelled,
    mark_started,
    mark_succeeded,
    set_phase,
)
from apps.tasks.models import TaskRun


def _run(task_run_id: str | None) -> TaskRun | None:
    if not task_run_id:
        return None
    return TaskRun.objects.filter(public_id=task_run_id).first()


@shared_task(name="indexing.encode_corpus")
def encode_corpus(
    task_run_id: str | None = None,
    corpus_id: int | None = None,
    encoder_id: int | None = None,
    **kwargs,
):
    run = _run(task_run_id)
    if run:
        run = mark_started(run)
        if run.status == "cancelled":
            return
        set_phase(run, "planning")
    corpus = Corpus.objects.get(pk=corpus_id)
    encoder = Encoder.objects.get(pk=encoder_id)
    pending = content_needing_encoding(corpus, encoder)
    batches = plan_batches(pending)
    if run:
        already = run.progress_current or 0
        TaskRun.objects.filter(pk=run.pk).update(progress_total=already + len(pending))
        set_phase(run, "dispatching")
    for batch in batches:
        if run:
            run.refresh_from_db()
            if run.cancel_requested_at:
                mark_cancelled(run)
                return
        dispatch(
            "indexing.encode_batch",
            {
                "content_ids": batch,
                "encoder_id": encoder.pk,
                "parent_task_run_id": str(run.public_id) if run else None,
            },
            scope_token=f"{corpus.pk}:{encoder.pk}:{batch[0]}",
            requested_by=run.requested_by if run else None,
        )
    if run and not batches:
        mark_succeeded(run)


@shared_task(name="indexing.encode_batch")
def encode_batch(
    task_run_id: str | None = None,
    content_ids: list[int] | None = None,
    encoder_id: int | None = None,
    parent_task_run_id: str | None = None,
    **kwargs,
):
    run = _run(task_run_id)
    if run:
        run = mark_started(run)
        if run.status == "cancelled":
            return
        set_phase(run, "loading")
    encoder = Encoder.objects.get(pk=encoder_id)
    parent = _run(parent_task_run_id)
    if parent and parent.cancel_requested_at:
        if run:
            mark_cancelled(run)
        return
    ids = content_ids or []
    encoded = 0
    if run:
        TaskRun.objects.filter(pk=run.pk).update(progress_total=len(ids), progress_current=0)
        set_phase(run, "encoding")
    for pk in ids:
        content = ContentObject.objects.get(pk=pk)
        if encode_content(content, encoder) is not None:
            encoded += 1
            if run:
                TaskRun.objects.filter(pk=run.pk).update(progress_current=encoded)
        if parent:
            increment_progress(parent, 1)
            elect_parent_completion(parent)
    if run:
        set_phase(run, "persisting")
        mark_succeeded(run)
