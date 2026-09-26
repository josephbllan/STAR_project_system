"""Fuse per-encoder scores and an optional metadata score.

The formula is:

 s_model = λ · s_dinov2 + (1 − λ) · s_clip
 s_final = α · s_model + (1 − α) · s_metadata

where `model_weight` is λ and `metadata_weight` is (1 − α), matching the columns on `cases_run`. A missing component is omitted from the weighted mean of the parts that are present,
rather than treated as zero: a zero would pull every CLIP-only result toward the origin.
"""

from __future__ import annotations

from retrieval.types import FusedResult, ScoredCandidate


def fuse(
    candidates: list[ScoredCandidate],
    *,
    model_weight: float,
    metadata_weight: float,
    metadata_scores: dict | None = None,
) -> list[FusedResult]:
    if not 0 <= model_weight <= 1 or not 0 <= metadata_weight <= 1:
        raise ValueError("fusion weights must lie in [0, 1]")

    by_content: dict = {}
    for candidate in candidates:
        by_content.setdefault(candidate.content_id, {})[candidate.encoder.name] = candidate.score

    alpha = 1.0 - metadata_weight
    results: list[FusedResult] = []
    for content_id, parts in by_content.items():
        clip = _named(parts, "clip")
        dinov2 = _named(parts, "dinov2")
        if clip is None and dinov2 is None:
            # Fall back to whatever encoder names we actually have.
            values = list(parts.values())
            s_model = sum(values) / len(values)
        elif clip is None:
            s_model = dinov2
        elif dinov2 is None:
            s_model = clip
        else:
            s_model = model_weight * dinov2 + (1.0 - model_weight) * clip

        meta = (metadata_scores or {}).get(content_id)
        s_final = s_model if meta is None else alpha * s_model + metadata_weight * meta
        components = dict(parts)
        components["model"] = s_model
        if meta is not None:
            components["metadata"] = meta
        results.append(
            FusedResult(content_id=content_id, fused_score=s_final, components=components)
        )

    results.sort(key=lambda item: item.fused_score, reverse=True)
    return results


def _named(parts: dict[str, float], family: str) -> float | None:
    for name, score in parts.items():
        if family in name.lower():
            return score
    return None
