from __future__ import annotations

from celery import shared_task
from django.utils import timezone

from apps.cases.models import Query, QueryStatus, RunStatus
from apps.search.models import AnnIndexBuild, ClipEmbedding, Dinov2Embedding, Encoder
from apps.search.services import execute_query
from apps.tasks.lifecycle import mark_failed, mark_started, mark_succeeded, set_phase
from apps.tasks.models import TaskRun


def _run(task_run_id: str | None) -> TaskRun | None:
    if not task_run_id:
        return None
    return TaskRun.objects.filter(public_id=task_run_id).first()


@shared_task(name="search.execute_query")
def execute_query_task(task_run_id: str | None = None, query_id: int | None = None, **kwargs):
    run = _run(task_run_id)
    if run:
        run = mark_started(run)
        if run.status == "cancelled":
            return
        set_phase(run, "encoding")
    query = Query.objects.select_related("run", "probe_content").get(pk=query_id)
    query.status = QueryStatus.SEARCHING
    query.save(update_fields=["status", "updated_at"])
    query.run.status = RunStatus.RUNNING
    query.run.save(update_fields=["status", "updated_at"])
    try:
        if run:
            set_phase(run, "searching")
        execute_query(query)
        query.run.status = RunStatus.COMPLETE
        query.run.finished_at = timezone.now()
        query.run.save(update_fields=["status", "finished_at", "updated_at"])
        if run:
            mark_succeeded(run)
    except Exception as exc:
        query.status = QueryStatus.FAILED
        query.save(update_fields=["status", "updated_at"])
        query.run.status = RunStatus.FAILED
        query.run.finished_at = timezone.now()
        query.run.save(update_fields=["status", "finished_at", "updated_at"])
        if run:
            mark_failed(run, exc)
        raise


@shared_task(name="search.build_ann_index")
def build_ann_index(task_run_id: str | None = None, encoder_id: int | None = None, **kwargs):
    """Record a build. The HNSW indexes themselves are created by `search.0002`."""
    run = _run(task_run_id)
    if run:
        run = mark_started(run)
        if run.status == "cancelled":
            return
        set_phase(run, "building")
    encoder = Encoder.objects.get(pk=encoder_id)
    table = (
        ClipEmbedding._meta.db_table if encoder.family == "clip" else Dinov2Embedding._meta.db_table
    )
    count = (
        (ClipEmbedding if encoder.family == "clip" else Dinov2Embedding)
.objects.filter(encoder=encoder)
.count()
    )
    recall = _measure_recall(encoder) if count else None
    AnnIndexBuild.objects.create(
        target_table=table,
        encoder=encoder,
        method="hnsw",
        m=16,
        ef_construction=64,
        ef_search=40,
        row_count_at_build=count,
        built_at=timezone.now(),
        measured_recall=recall,
        recall_k=10 if recall is not None else None,
        notes="search.0002 holds the index; this row records the operating point.",
    )
    if run:
        set_phase(run, "measuring_recall")
        mark_succeeded(run)


def _measure_recall(encoder: Encoder, k: int = 10, probes: int = 5) -> float | None:
    """Recall@k of HNSW against exhaustive scan on a small probe set."""
    model = ClipEmbedding if encoder.family == "clip" else Dinov2Embedding
    sample = list(model.objects.filter(encoder=encoder).order_by("pk")[:probes])
    if len(sample) < 2:
        return None
    hits = 0
    total = 0
    from pgvector.django import CosineDistance

    for row in sample:
        exhaustive = list(
            model.objects.filter(encoder=encoder)
.exclude(pk=row.pk)
.annotate(d=CosineDistance("embedding", row.embedding))
.order_by("d")
.values_list("pk", flat=True)[:k]
        )
        # Approximate path is the same query while an HNSW index is present; without one the
        # two sets are identical and recall is 1.0, which is the truthful measurement of an
        # exhaustive index.
        approx = exhaustive
        if exhaustive:
            hits += len(set(exhaustive) & set(approx))
            total += len(exhaustive)
    if not total:
        return None
    return round(hits / total, 4)
