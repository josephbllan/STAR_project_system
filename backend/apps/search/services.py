"""Execute a query: encode the probe, search, over-fetch, filter, fuse, persist."""

from __future__ import annotations

from django.db.models import Q

from apps.cases.models import Query, QueryStatus, QueryType, Result, Run
from apps.datasets.models import EvidenceFile, EvidenceState
from apps.indexing.adapters import OrmVectorStore
from retrieval.encoders.clip import load_clip
from retrieval.encoders.dinov2 import load_dinov2
from retrieval.fusion import fuse
from retrieval.query_router import route_image
from retrieval.types import EncoderRef, ScoredCandidate


def execute_query(query: Query) -> list[Result]:
    run: Run = query.run
    store = OrmVectorStore()
    overfetch = min(run.top_k * run.overfetch_factor, run.overfetch_ceiling)

    # `run.corpora` is the RunCorpus junction; `pk` is that row, not the corpus.
    corpus_ids = list(run.corpora.values_list("corpus_id", flat=True))
    eligible = EvidenceFile.objects.filter(
        state__in=[EvidenceState.INDEXED, EvidenceState.REGISTERED]
    )
    if corpus_ids:
        eligible = eligible.filter(corpus_id__in=corpus_ids)
    # Case-scoped evidence is visible only inside its case; shared (null case) is always in.
    eligible = eligible.filter(Q(case__isnull=True) | Q(case_id=run.case_id))
    content_ids = set(eligible.values_list("content__public_id", flat=True))

    candidates: list[ScoredCandidate] = []
    clip = run.clip_encoder
    dinov2 = run.dinov2_encoder

    if query.query_type == QueryType.IMAGE:
        from apps.common import storage

        with storage.open_stored(query.probe_content.storage_key) as handle:
            data = handle.read()
        query.routed_spectrum = route_image(data)
        query.save(update_fields=["routed_spectrum", "updated_at"])
        if clip:
            values = load_clip().encode_image(data)
            candidates.extend(
                store.search(
                    values,
                    EncoderRef(clip.name, clip.dimensions),
                    limit=overfetch,
                    content_ids=content_ids or None,
                )
            )
        if dinov2:
            values = load_dinov2().encode_image(data)
            candidates.extend(
                store.search(
                    values,
                    EncoderRef(dinov2.name, dinov2.dimensions),
                    limit=overfetch,
                    content_ids=content_ids or None,
                )
            )
    else:
        text = query.query_text or ""
        if clip:
            values = load_clip().encode_text(text)
            candidates.extend(
                store.search(
                    values,
                    EncoderRef(clip.name, clip.dimensions),
                    limit=overfetch,
                    content_ids=content_ids or None,
                )
            )
        if dinov2:
            values = load_dinov2().encode_text(text)
            candidates.extend(
                store.search(
                    values,
                    EncoderRef(dinov2.name, dinov2.dimensions),
                    limit=overfetch,
                    content_ids=content_ids or None,
                )
            )

    fused = fuse(
        candidates,
        model_weight=float(run.model_weight),
        metadata_weight=float(run.metadata_weight),
    )[: run.top_k]

    written: list[Result] = []
    for rank, item in enumerate(fused, start=1):
        hits = list(
            eligible.filter(content__public_id=item.content_id).select_related("content")
        )
        if not hits:
            continue
        # Prefer the shared indexed original over a case-scoped probe copy.
        evidence = sorted(
            hits,
            key=lambda row: (
                row.case_id is not None,
                row.state != EvidenceState.INDEXED,
                row.pk,
            ),
        )[0]
        parts = item.components
        written.append(
            Result.objects.create(
                query=query,
                rank=rank,
                evidence_file=evidence,
                score_fused=item.fused_score,
                score_model=parts.get("model", item.fused_score),
                score_clip=_family_score(parts, "clip"),
                score_dinov2=_family_score(parts, "dinov2"),
                score_metadata=parts.get("metadata"),
            )
        )
    query.result_count = len(written)
    query.status = QueryStatus.COMPLETE
    query.save(update_fields=["result_count", "status", "updated_at"])
    return written


def _family_score(parts: dict, family: str) -> float | None:
    for name, score in parts.items():
        if family in name.lower() and name not in {"model", "metadata"}:
            return score
    return None
