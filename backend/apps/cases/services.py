"""Case lifecycle, membership and retrieval-run creation."""

from __future__ import annotations

from django.utils import timezone

from apps.audit.models import AuditAction
from apps.audit.recorder import record
from apps.cases.models import (
    Case,
    CaseMembership,
    CaseStatus,
    Query,
    QueryType,
    Run,
    RunCorpus,
)
from apps.review.sanitisation import sanitise
from apps.search.models import Encoder, EncoderFamily
from apps.tasks.dispatch import dispatch


def create_case(*, owner, name: str, reference: str = "", description: str = "") -> Case:
    case = Case.objects.create(
        owner=owner,
        name=name,
        reference=reference,
        description=sanitise(description),
    )
    record(AuditAction.CASE_CREATED, actor=owner, target=case)
    return case


def set_case_status(case: Case, *, status: str, actor) -> Case:
    case.status = status
    case.status_changed_at = timezone.now()
    case.closed_at = timezone.now() if status == CaseStatus.CLOSED else None
    case.save(update_fields=["status", "status_changed_at", "closed_at", "updated_at"])
    record(AuditAction.CASE_STATUS_CHANGED, actor=actor, target=case, detail={"status": status})
    return case


def grant_membership(*, case: Case, user, access_level: str, granted_by) -> CaseMembership:
    membership = CaseMembership.objects.create(
        case=case, user=user, access_level=access_level, granted_by=granted_by
    )
    record(
        AuditAction.MEMBERSHIP_GRANTED,
        actor=granted_by,
        target=case,
        detail={"member": user.username, "access_level": access_level},
    )
    return membership


def revoke_membership(membership: CaseMembership, *, revoked_by) -> CaseMembership:
    membership.revoked_at = timezone.now()
    membership.revoked_by = revoked_by
    membership.save(update_fields=["revoked_at", "revoked_by", "updated_at"])
    record(AuditAction.MEMBERSHIP_REVOKED, actor=revoked_by, target=membership.case)
    return membership


def create_run(
    *,
    case: Case,
    created_by,
    label: str,
    top_k: int = 20,
    model_weight="0.500",
    metadata_weight="0.000",
    corpus_ids: list[int] | None = None,
    use_clip: bool = True,
    use_dinov2: bool = True,
) -> Run:
    clip = (
        Encoder.objects.filter(family=EncoderFamily.CLIP, is_active=True).first()
        if use_clip
        else None
    )
    dinov2 = (
        Encoder.objects.filter(family=EncoderFamily.DINOV2, is_active=True).first()
        if use_dinov2
        else None
    )
    if clip is None and dinov2 is None:
        clip = Encoder.objects.filter(family=EncoderFamily.CLIP, is_active=True).first()
        dinov2 = Encoder.objects.filter(family=EncoderFamily.DINOV2, is_active=True).first()
    run = Run.objects.create(
        case=case,
        label=label,
        created_by=created_by,
        top_k=top_k,
        model_weight=model_weight,
        metadata_weight=metadata_weight,
        clip_encoder=clip,
        dinov2_encoder=dinov2,
    )
    for corpus_id in corpus_ids or []:
        RunCorpus.objects.create(run=run, corpus_id=corpus_id)
    record(AuditAction.RUN_CREATED, actor=created_by, target=run)
    return run


def submit_query(
    *, run: Run, created_by, query_type: str, probe_content=None, query_text=None
) -> Query:
    sequence = (
        run.queries.order_by("-sequence").values_list("sequence", flat=True).first() or 0
    ) + 1
    query = Query.objects.create(
        run=run,
        sequence=sequence,
        query_type=query_type,
        probe_content=probe_content if query_type == QueryType.IMAGE else None,
        query_text=query_text if query_type == QueryType.TEXT else None,
    )
    record(AuditAction.QUERY_SUBMITTED, actor=created_by, target=query)
    dispatch(
        "search.execute_query",
        {"query_id": query.pk, "target_public_id": str(query.public_id)},
        scope_token=str(query.public_id),
        requested_by=created_by,
        target_type="query",
        target_public_id=query.public_id,
    )
    return query


def save_run(run: Run, *, actor=None) -> Run:
    """Mark a completed retrieval run as history so other screens can reload it."""
    params = dict(run.params or {})
    params["saved"] = True
    run.params = params
    run.save(update_fields=["params", "updated_at"])
    record(AuditAction.RUN_CREATED, actor=actor, target=run, detail={"saved": True})
    return run
