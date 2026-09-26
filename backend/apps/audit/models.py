"""The audit trail: the principal integrity control in the system.

This table differs from every other one in four ways, and each difference is deliberate.

**It is append-only by database privilege, not by convention.** `apps.audit.migrations.0002` revokes
`UPDATE` and `DELETE` from the application role. The application cannot rewrite history even if a
code path tries, which is what makes the trail evidence rather than a log.

**It carries `occurred_at` instead of `created_at`, and has no `updated_at`.** A modification time
on an append-only table would be a column that can only ever hold one value while implying
otherwise. This is the one table that does not extend `TimeStampedModel`.

**The action taxonomy is an enumeration, not a string composed at the call site**. A
constrained vocabulary is what makes the trail queryable years later: `evidence.accessed` means one
thing, whereas a call site free to write `"accessed evidence"` produces a trail that has to be
grepped rather than filtered. An action outside the taxonomy is refused rather than recorded, so the
taxonomy cannot erode one call site at a time.

**The actor is recorded twice: as a foreign key and as a text snapshot**. The key preserves
attribution and refuses to let the account be deleted. The snapshot preserves what was true at the
time, because a username change must not silently rewrite history and a deactivated account must not
orphan an event.

**What is not here.** A tamper-evident hash chain - each row carrying the digest of its
predecessor - is recommended for release 2 and deliberately not adopted now. It would
strengthen the evidential claim beyond privilege separation, but it requires inserts to be
serialised, which contradicts concurrent insertion from several API processes and several workers.
It is recorded as deferred rather than omitted, so that nobody concludes append-only privilege was
assumed sufficient without examination.
"""

from __future__ import annotations

from django.conf import settings
from django.db import models
from django.db.models import Q
from django.db.models.functions import Now
from django.utils import timezone

from apps.common.models import PublicIdModel


class AuditAction(models.TextChoices):
    """Closed vocabulary of audit actions.

 Grouped by prefix so that monitoring can filter on a family - `action LIKE 'auth.%'` - without
 the groups being a second thing to keep in step with the values.
 """

    # Authentication
    LOGIN_SUCCEEDED = "auth.login.succeeded", "Login succeeded"
    LOGIN_FAILED = "auth.login.failed", "Login failed"
    LOGOUT = "auth.logout", "Logout"
    MFA_ENROLLED = "auth.mfa.enrolled", "Second factor enrolled"
    MFA_FAILED = "auth.mfa.failed", "Second factor failed"
    LOCKED_OUT = "auth.locked_out", "Locked out"
    PASSWORD_CHANGED = "auth.password.changed", "Password changed"

    # Authorisation
    AUTHZ_DENIED = "authz.denied", "Authorisation denied"
    ROLE_CHANGED = "authz.role.changed", "Role changed"
    MEMBERSHIP_GRANTED = "authz.membership.granted", "Membership granted"
    MEMBERSHIP_REVOKED = "authz.membership.revoked", "Membership revoked"

    # Evidence
    EVIDENCE_REGISTERED = "evidence.registered", "Evidence registered"
    EVIDENCE_ACCESSED = "evidence.accessed", "Evidence accessed"
    EVIDENCE_URL_ISSUED = "evidence.url.issued", "Evidence URL issued"
    EVIDENCE_PROCESSED = "evidence.processed", "Evidence processed"
    EVIDENCE_METADATA_CHANGED = "evidence.metadata.changed", "Evidence metadata changed"
    INTEGRITY_VERIFIED = "evidence.integrity.verified", "Integrity verified"
    INTEGRITY_FAILED = "evidence.integrity.failed", "Integrity check failed"
    EVIDENCE_QUARANTINED = "evidence.quarantined", "Evidence quarantined"

    # Case
    CASE_CREATED = "case.created", "Case created"
    CASE_STATUS_CHANGED = "case.status.changed", "Case status changed"
    CASE_METADATA_CHANGED = "case.metadata.changed", "Case metadata changed"
    CASE_OWNER_CHANGED = "case.owner.changed", "Case owner changed"

    # Retrieval
    RUN_CREATED = "search.run.created", "Run created"
    QUERY_SUBMITTED = "search.query.submitted", "Query submitted"
    RESULTS_VIEWED = "search.results.viewed", "Results viewed"

    # Review
    RATING_RECORDED = "review.rating.recorded", "Rating recorded"
    NOTE_RECORDED = "review.note.recorded", "Note recorded"
    NOTE_SUPERSEDED = "review.note.superseded", "Note superseded"
    APPROVAL_REQUESTED = "review.approval.requested", "Approval requested"
    APPROVAL_DECIDED = "review.approval.decided", "Approval decided"
    APPROVAL_COUNTERSIGNED = "review.approval.countersigned", "Approval countersigned"
    APPROVAL_WITHDRAWN = "review.approval.withdrawn", "Approval withdrawn"

    # Export
    EXPORT_REQUESTED = "export.requested", "Export requested"
    EXPORT_COMPLETED = "export.completed", "Export completed"
    EXPORT_FAILED = "export.failed", "Export failed"
    EXPORT_DOWNLOADED = "export.downloaded", "Export downloaded"

    # Administration
    USER_CREATED = "admin.user.created", "User created"
    USER_DEACTIVATED = "admin.user.deactivated", "User deactivated"
    SETTING_CHANGED = "admin.setting.changed", "Setting changed"
    CORPUS_CREATED = "admin.corpus.created", "Corpus created"
    ENCODER_REGISTERED = "admin.encoder.registered", "Encoder registered"

    # Audit. Reading the trail is itself audited: once per query with the filter
    # parameters in `detail`, not once per row returned.
    AUDIT_READ = "audit.read", "Audit trail read"


class AuditOutcome(models.TextChoices):
    SUCCEEDED = "succeeded", "Succeeded"
    DENIED = "denied", "Denied"
    FAILED = "failed", "Failed"


class AuditEvent(PublicIdModel):
    """One recorded event. Never updated, never deleted."""

    #: Replaces `created_at`, and there is deliberately no `updated_at`: see the module docstring.
    occurred_at = models.DateTimeField(default=timezone.now, db_default=Now())
    action = models.CharField(max_length=64, choices=AuditAction.choices)

    #: Null for an anonymous or system actor - a failed login before the username is known, or a
    #: scheduled integrity sweep. The check below still requires one of the two forms of
    #: attribution, because an event with none is not an audit record.
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="audit_events",
        db_index=False,
    )
    #: Snapshots, not denormalisation for speed. What was true at the time.
    actor_username = models.CharField(max_length=150, default="", blank=True)
    actor_role = models.CharField(max_length=20, default="", blank=True)

    #: Generic by design: the trail has to record events about entities that do not yet
    #: exist in the schema, so the target cannot be a foreign key. The numeric key is recorded
    #: additionally where available, for joining.
    target_type = models.CharField(max_length=50, default="", blank=True)
    target_public_id = models.UUIDField(null=True, blank=True)
    target_id = models.BigIntegerField(null=True, blank=True)
    #: A non-sensitive descriptor. Never evidential content - not a filename, not a note body.
    target_label = models.CharField(max_length=200, default="", blank=True)

    correlation_id = models.UUIDField(db_index=False)
    request_method = models.CharField(max_length=10, default="", blank=True)
    request_path = models.CharField(max_length=300, default="", blank=True)
    #: Personal data. Its retention window is stated in and is shorter
    #: than the trail's own.
    source_ip = models.GenericIPAddressField(null=True, blank=True)

    outcome = models.CharField(max_length=16, choices=AuditOutcome.choices)
    #: Changed field names and filter parameters. **Never** a field value that is evidential,
    #: personal or secret. Constrained by convention and by test, not by shape.
    detail = models.JSONField(default=dict)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["public_id"], name="uq_audit_auditevent_public_id"),
            # An action outside the taxonomy is rejected rather than recorded, which is what stops
            # the taxonomy eroding one call site at a time.
            models.CheckConstraint(
                condition=Q(action__in=[a.value for a in AuditAction]),
                name="ck_audit_auditevent_action_valid",
            ),
            models.CheckConstraint(
                condition=Q(outcome__in=[o.value for o in AuditOutcome]),
                name="ck_audit_auditevent_outcome_valid",
            ),
            models.CheckConstraint(
                condition=Q(actor__isnull=False) | ~Q(actor_username=""),
                name="ck_audit_auditevent_actor_identified",
            ),
        ]
        indexes = [
            # : volume of access and export per user per period. Leading columns match the
            # filter, trailing column the ordering.
            models.Index(
                fields=["actor", "action", "-occurred_at"],
                name="ix_audit_auditevent_actor_action_time",
            ),
            # The chain-of-custody view for one item of evidence: the query a court challenge
            # produces.
            models.Index(
                fields=["target_type", "target_public_id", "-occurred_at"],
                name="ix_audit_auditevent_target",
            ),
            # Authentication-failure and authorisation-failure monitoring.
            models.Index(fields=["action", "-occurred_at"], name="ix_audit_auditevent_action_time"),
            models.Index(fields=["correlation_id"], name="ix_audit_auditevent_correlation"),
            # Cursor pagination of the audit collection, which is unbounded and therefore not
            # paginated by page number.
            models.Index(fields=["-occurred_at"], name="ix_audit_auditevent_occurred_at"),
        ]

    def __str__(self) -> str:
        return f"{self.action} {self.outcome} at {self.occurred_at:%Y-%m-%dT%H:%M:%SZ}"
