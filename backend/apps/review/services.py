"""Ratings, notes, tags and approvals. Nothing is edited in place."""

from __future__ import annotations

from django.utils import timezone

from apps.audit.models import AuditAction
from apps.audit.recorder import record
from apps.review.models import Approval, ApprovalState, Note, Rating, Tag, TagAssignment
from apps.review.sanitisation import sanitise


def record_rating(*, author, value: int, scope: str, **targets) -> Rating:
    rating, _ = Rating.objects.update_or_create(
        author=author,
        scope=scope,
        **{k: v for k, v in targets.items() if v is not None},
        defaults={"value": value},
    )
    record(AuditAction.RATING_RECORDED, actor=author, target=rating, detail={"value": value})
    return rating


def record_note(*, author, body: str, scope: str, parent=None, **targets) -> Note:
    note = Note.objects.create(
        author=author,
        body=sanitise(body),
        scope=scope,
        parent=parent,
        **{k: v for k, v in targets.items() if v is not None},
    )
    record(AuditAction.NOTE_RECORDED, actor=author, target=note)
    return note


def supersede_note(note: Note, *, author, body: str) -> Note:
    replacement = record_note(
        author=author,
        body=body,
        scope=note.scope,
        parent=note.parent,
        case=note.case,
        run=note.run,
        query=note.query,
        result=note.result,
    )
    note.superseded_by = replacement
    note.superseded_at = timezone.now()
    note.save(update_fields=["superseded_by", "superseded_at", "updated_at"])
    record(AuditAction.NOTE_SUPERSEDED, actor=author, target=note)
    return replacement


def assign_tag(*, tag: Tag, assigned_by, scope: str, **targets) -> TagAssignment:
    assignment, _ = TagAssignment.objects.get_or_create(
        tag=tag,
        scope=scope,
        assigned_by=assigned_by,
        **{k: v for k, v in targets.items() if v is not None},
    )
    return assignment


def toggle_validation_approval(*, result, actor) -> Approval:
    """Search Validation click: approve if unset, withdraw if already approved."""
    approval = Approval.objects.filter(result=result).first()
    if approval is None:
        approval = request_approval(result=result, requested_by=actor)
        return decide_approval(approval, decided_by=actor, approved=True)
    if approval.state in {ApprovalState.APPROVED, ApprovalState.COUNTERSIGNED}:
        return withdraw_approval(approval, withdrawn_by=actor)
    return decide_approval(approval, decided_by=actor, approved=True)


def request_approval(
    *, result, requested_by, evidence_label: str = "", notes: str = ""
) -> Approval:
    approval = Approval.objects.create(
        result=result,
        requested_by=requested_by,
        evidence_label=sanitise(evidence_label),
        notes=sanitise(notes),
    )
    record(AuditAction.APPROVAL_REQUESTED, actor=requested_by, target=approval)
    return approval


def decide_approval(approval: Approval, *, decided_by, approved: bool) -> Approval:
    approval.state = ApprovalState.APPROVED if approved else ApprovalState.REJECTED
    approval.decided_by = decided_by
    approval.decided_at = timezone.now()
    approval.save(update_fields=["state", "decided_by", "decided_at", "updated_at"])
    record(
        AuditAction.APPROVAL_DECIDED,
        actor=decided_by,
        target=approval,
        detail={"approved": approved},
    )
    return approval


def countersign_approval(approval: Approval, *, countersigned_by) -> Approval:
    approval.state = ApprovalState.COUNTERSIGNED
    approval.countersigned_by = countersigned_by
    approval.countersigned_at = timezone.now()
    approval.save(update_fields=["state", "countersigned_by", "countersigned_at", "updated_at"])
    record(AuditAction.APPROVAL_COUNTERSIGNED, actor=countersigned_by, target=approval)
    return approval


def withdraw_approval(approval: Approval, *, withdrawn_by) -> Approval:
    approval.state = ApprovalState.WITHDRAWN
    approval.withdrawn_by = withdrawn_by
    approval.withdrawn_at = timezone.now()
    approval.save(update_fields=["state", "withdrawn_by", "withdrawn_at", "updated_at"])
    record(AuditAction.APPROVAL_WITHDRAWN, actor=withdrawn_by, target=approval)
    return approval
