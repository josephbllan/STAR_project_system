"""Review and approval.

**The scope discriminator.** Ratings, tag assignments and notes each attach to one of four
things: a case, a run, a query or a result. That is expressed as a `scope` column plus four nullable
foreign keys, with a check constraint requiring that exactly one is populated *and* that it is the
one the discriminator names.

A generic content-type reference - Django's `GenericForeignKey` - was rejected. It would have been
one nullable pair of columns instead of four, but it defeats referential integrity: the database
cannot enforce that the referenced row exists, cannot refuse to delete it, and cannot join through
it without a subquery per content type. Four foreign keys and a wordy constraint keep
referential integrity, which a generic content-type pair would not.

**Nothing here is edited in place.** A note is amended by writing a new note and setting
`superseded_by` on the old one. An approval is withdrawn by a state transition with
its own actor and timestamp, not by deletion or reversal. The reason is the same in both
cases: this app records what people concluded about evidence, and a record that can be quietly
changed afterwards cannot be cited.

**Self-countersignature is prevented by the database**. A control implemented in a service
method is defeated by the second call site that forgets it, and the second call site always arrives.

**Index policy, and a trade-off taken the other way here.** Elsewhere Django's implicit foreign-key
index is suppressed wherever a named index already leads with that key. On the three scoped tables
it is kept on all four scope columns, for two reasons. Only `result` (and `case`, partially) has a
named index at all, so suppressing the rest would leave two scopes with no index for the commonest
question asked of them - "what is attached to this run?". And varying one field argument per
subclass would mean overriding an inherited field in each of three models, repeating every other
argument, which costs more clarity than the overlapping index on `review_rating` costs disk: these
tables hold a handful of rows per target, not one per image.
"""

from __future__ import annotations

from django.conf import settings
from django.db import models
from django.db.models import F, Q
from django.db.models.functions import Now
from django.utils import timezone

from apps.common.models import PublicIdModel, TimeStampedModel
from apps.common.querysets import ScopedManager

MIN_RATING = 1
MAX_RATING = 5
MAX_NOTE_LENGTH = 20_000


class Scope(models.TextChoices):
    CASE = "case", "Case"
    RUN = "run", "Run"
    QUERY = "query", "Query"
    RESULT = "result", "Result"


class TagType(models.TextChoices):
    PRIORITY = "priority", "Priority"
    STATUS = "status", "Status"
    CUSTOM = "custom", "Custom"


class ApprovalState(models.TextChoices):
    REQUESTED = "requested", "Requested"
    APPROVED = "approved", "Approved"
    REJECTED = "rejected", "Rejected"
    COUNTERSIGNED = "countersigned", "Countersigned"
    WITHDRAWN = "withdrawn", "Withdrawn"


#: Built once from `Scope` so the constraint and the property below cannot disagree about which
#: column each scope owns.
_SCOPE_FIELDS: dict[str, str] = {
    Scope.CASE: "case",
    Scope.RUN: "run",
    Scope.QUERY: "query",
    Scope.RESULT: "result",
}


def _exactly_one_target() -> Q:
    """The shared scope constraint, assembled rather than written out four times.

    Generated from `_SCOPE_FIELDS` so each branch asserts its own column is present and
    every other column is absent, and neither list can fall out of step with the
    enumeration.
    """
    condition = Q()
    for scope, field in _SCOPE_FIELDS.items():
        branch = Q(scope=scope, **{f"{field}__isnull": False})
        for other in _SCOPE_FIELDS.values():
            if other != field:
                branch &= Q(**{f"{other}__isnull": True})
        condition |= branch
    return condition


class ScopedModel(models.Model):
    """The four-target scope shared by ratings, tag assignments and notes."""

    scope = models.CharField(max_length=8, choices=Scope.choices)

    case = models.ForeignKey(
        "cases.Case",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="%(class)ss",
    )
    run = models.ForeignKey(
        "cases.Run",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="%(class)ss",
    )
    query = models.ForeignKey(
        "cases.Query",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="%(class)ss",
    )
    result = models.ForeignKey(
        "cases.Result",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="%(class)ss",
    )

    # Four paths, because three of the four columns are null on any given row and the case a
    # scoped record belongs to is reached through whichever one is populated. Declaring all four and
    # letting the joins decide is shorter than a discriminator lookup and cannot disagree with the
    # check constraint about which column counts.
    objects = ScopedManager(
        case_paths=("case", "run__case", "query__run__case", "result__query__run__case")
    )

    class Meta:
        abstract = True
        constraints = [
            models.CheckConstraint(
                condition=_exactly_one_target(), name="ck_review_%(class)s_scope_target"
            ),
        ]

    @property
    def target(self) -> models.Model:
        """The one populated target, resolved through the discriminator rather than by testing each
 column in turn - which is what a reader would otherwise have to do, in an order that would
 quietly become the precedence rule."""
        return getattr(self, _SCOPE_FIELDS[self.scope])


def _scope_unique(table: str, second: str) -> list[models.UniqueConstraint]:
    """One partial unique constraint per scope.

 Four constraints rather than one on `(scope, case_id, run_id, query_id, result_id, author_id)`,
 because in PostgreSQL a unique constraint treats nulls as distinct: a single constraint over all
 four columns would permit any number of duplicate rows, since three of the four are always null.
 That is the classic way this table shape goes wrong, and it fails silently.
 """
    return [
        models.UniqueConstraint(
            fields=[field_name, second],
            condition=Q(scope=scope),
            name=f"uq_review_{table}_{scope}_{second}",
        )
        for scope, field_name in _SCOPE_FIELDS.items()
    ]


class Rating(TimeStampedModel, ScopedModel):
    """An integer of one to five, attributed, unique per author and target.

 A revised rating replaces the author's own row. The previous value is recoverable from the audit
 trail, which is where the history of an opinion belongs - unlike a note, where the superseded
 text itself has to remain readable because it was a statement about the evidence.
 """

    value = models.SmallIntegerField()
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="ratings"
    )

    class Meta(ScopedModel.Meta):
        abstract = False
        constraints = [
            *ScopedModel.Meta.constraints,
            models.CheckConstraint(
                condition=Q(value__gte=MIN_RATING) & Q(value__lte=MAX_RATING),
                name="ck_review_rating_value_range",
            ),
            *_scope_unique("rating", "author"),
        ]
        indexes = [
            # The aggregate rating shown on each result row. Partial, and the condition is implied
            # by any query that filters on `result_id`, so the implicit index is suppressed below.
            models.Index(
                fields=["result"],
                condition=Q(result__isnull=False),
                name="ix_review_rating_result",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.value}/5 by {self.author_id}"


class Tag(TimeStampedModel):
    """A vocabulary item.

 Split from `TagAssignment` so that the label of a tag cannot be edited on one target and not
 another - which is what happens when the label is a column on the assignment.
 """

    tag_type = models.CharField(max_length=16, choices=TagType.choices)
    label = models.CharField(max_length=100)
    colour = models.CharField(max_length=16, default="", blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["tag_type", "label"], name="uq_review_tag_type_label"),
            models.CheckConstraint(
                condition=Q(tag_type__in=[t.value for t in TagType]),
                name="ck_review_tag_type_valid",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.tag_type}:{self.label}"


class TagAssignment(TimeStampedModel, ScopedModel):
    tag = models.ForeignKey(Tag, on_delete=models.PROTECT, related_name="assignments")
    assigned_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="tag_assignments"
    )

    class Meta(ScopedModel.Meta):
        abstract = False
        constraints = [
            *ScopedModel.Meta.constraints,
            *_scope_unique("tagassignment", "tag"),
        ]

    def __str__(self) -> str:
        return f"{self.tag_id} on {self.scope}"


class Note(TimeStampedModel, PublicIdModel, ScopedModel):
    """Hierarchical, pinnable, attributed, and never edited in place.

 An amendment writes a new note and sets `superseded_by` on the previous one, which remains
 readable. That is the difference between a record of what was thought at the time and a record
 of what someone currently wants it to have been.

 `body` holds the **sanitised** form and only the sanitised form. The raw
 submission is not retained, because retaining it invites a later code path that renders it - and
 that code path will be written by someone who assumes the column is safe.
 """

    parent = models.ForeignKey(
        "self", on_delete=models.PROTECT, null=True, blank=True, related_name="replies"
    )
    body = models.TextField()
    is_pinned = models.BooleanField(default=False)
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="notes"
    )

    superseded_by = models.ForeignKey(
        "self", on_delete=models.PROTECT, null=True, blank=True, related_name="supersedes"
    )
    superseded_at = models.DateTimeField(null=True, blank=True)

    class Meta(ScopedModel.Meta):
        abstract = False
        constraints = [
            *ScopedModel.Meta.constraints,
            # Missing from the first draft of this model and found by the schema-wide test in
            # `tests/database/test_constraint_closure.py`, which asserts that every table carrying a
            # public identifier enforces its uniqueness. A note is addressed by that identifier and
            # is cited by one in a report, so a duplicate would make two notes resolve to one URL
            # and a citation ambiguous.
            models.UniqueConstraint(fields=["public_id"], name="uq_review_note_public_id"),
            # At the database as well as the serializer, so that a management command or a
            # data migration cannot write a twenty-kilobyte-plus body.
            models.CheckConstraint(
                condition=Q(body__length__gte=1) & Q(body__length__lte=MAX_NOTE_LENGTH),
                name="ck_review_note_body_length",
            ),
            models.CheckConstraint(
                condition=Q(superseded_by__isnull=True, superseded_at__isnull=True)
                | Q(superseded_by__isnull=False, superseded_at__isnull=False),
                name="ck_review_note_supersession_paired",
            ),
            models.CheckConstraint(
                condition=Q(superseded_by__isnull=True) | ~Q(superseded_by=F("id")),
                name="ck_review_note_no_self_supersede",
            ),
        ]
        indexes = [
            # The current notes on a result, newest first. The `superseded_by IS NULL` condition is
            # not implied by filtering on `result_id`, so the implicit index on `result` is kept:
            # reading a superseded note is a legitimate request and has to be served too.
            models.Index(
                fields=["result", "-created_at"],
                condition=Q(superseded_by__isnull=True, result__isnull=False),
                name="ix_review_note_result_current",
            ),
            models.Index(
                fields=["case"],
                condition=Q(is_pinned=True, case__isnull=False),
                name="ix_review_note_case_pinned",
            ),
        ]

    def __str__(self) -> str:
        return f"note {self.public_id} by {self.author_id}"

    @property
    def is_current(self) -> bool:
        return self.superseded_by_id is None


class ResultOrder(TimeStampedModel):
    """An explicit ordering of results within a case.

 `uq_review_resultorder_case_position` is **deferrable initially deferred**, and it is the only
 deferrable constraint in the schema. Reordering necessarily passes through intermediate states:
 moving the third result to first position means two rows briefly claim position one, whichever
 order the updates are issued in. Deferring the check to commit is what makes a reorder a single
 statement-sequence rather than an exercise in shuffling through a temporary position nobody
 uses.
 """

    case = models.ForeignKey(
        "cases.Case", on_delete=models.PROTECT, related_name="result_order", db_index=False
    )
    result = models.ForeignKey("cases.Result", on_delete=models.PROTECT, related_name="orderings")
    position = models.IntegerField()
    ordered_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="result_orderings"
    )

    objects = ScopedManager(case_paths=("case",))

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["case", "result"], name="uq_review_resultorder_case_result"
            ),
            models.UniqueConstraint(
                fields=["case", "position"],
                name="uq_review_resultorder_case_position",
                deferrable=models.Deferrable.DEFERRED,
            ),
        ]

    def __str__(self) -> str:
        return f"{self.position} in case {self.case_id}"


class Approval(TimeStampedModel, PublicIdModel):
    """The evidential act.

 Every actor is a foreign key and not free text. A name in a text column is not
 an attribution: it cannot be verified, it does not change when the person's account is
 disabled, and two people with the same name are indistinguishable in it.

 Unique per result, because an approval is a decision about a piece of evidence and there is
 only one of those. A second opinion is a countersignature, which is columns on this row rather
 than a second row.
 """

    # A `ForeignKey` with a named unique constraint rather than a `OneToOneField`, which would
    # create a second unique index under a Django-generated name. One index, one name in the
    # constraint registry, one thing for a violation to be mapped to.
    result = models.ForeignKey(
        "cases.Result", on_delete=models.PROTECT, related_name="approvals", db_index=False
    )
    state = models.CharField(
        max_length=16, choices=ApprovalState.choices, default=ApprovalState.REQUESTED
    )
    evidence_label = models.CharField(max_length=200, default="", blank=True)
    notes = models.TextField(default="", blank=True)

    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="requested_approvals"
    )
    requested_at = models.DateTimeField(default=timezone.now, db_default=Now())

    decided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="decided_approvals",
        db_index=False,
    )
    decided_at = models.DateTimeField(null=True, blank=True)

    #: Second-person approval. Columns rather than a second row, because a countersignature
    #: is not an independent decision - it is an endorsement of one, and it has no meaning without
    #: the decision it endorses.
    countersigned_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="countersigned_approvals",
    )
    countersigned_at = models.DateTimeField(null=True, blank=True)

    withdrawn_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="withdrawn_approvals",
    )
    withdrawn_at = models.DateTimeField(null=True, blank=True)

    objects = ScopedManager(case_paths=("result__query__run__case",))

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["result"], name="uq_review_approval_result"),
            models.UniqueConstraint(fields=["public_id"], name="uq_review_approval_public_id"),
            models.CheckConstraint(
                condition=Q(state__in=[s.value for s in ApprovalState]),
                name="ck_review_approval_state_valid",
            ),
            # Any state past `requested` is a decision, and a decision without a decider and a time
            # is an act nobody is accountable for.
            models.CheckConstraint(
                condition=Q(state=ApprovalState.REQUESTED)
                | Q(decided_by__isnull=False, decided_at__isnull=False),
                name="ck_review_approval_decision_paired",
            ),
            models.CheckConstraint(
                condition=Q(countersigned_by__isnull=True, countersigned_at__isnull=True)
                | Q(countersigned_by__isnull=False, countersigned_at__isnull=False),
                name="ck_review_approval_countersign_paired",
            ),
            # In the database, not in a service method: a control against self-approval
            # that lives in application code is defeated by the second call site, and there is
            # always a second call site.
            models.CheckConstraint(
                condition=Q(countersigned_by__isnull=True) | ~Q(countersigned_by=F("decided_by")),
                name="ck_review_approval_no_self_countersign",
            ),
            models.CheckConstraint(
                condition=Q(countersigned_by__isnull=True) | Q(decided_by__isnull=False),
                name="ck_review_approval_countersign_requires_decision",
            ),
            models.CheckConstraint(
                condition=Q(withdrawn_by__isnull=True, withdrawn_at__isnull=True)
                | Q(withdrawn_by__isnull=False, withdrawn_at__isnull=False),
                name="ck_review_approval_withdrawal_paired",
            ),
        ]
        indexes = [
            models.Index(fields=["state", "requested_at"], name="ix_review_approval_state"),
            # Attribution review, and the audit of one individual's approvals - which is a question
            # someone will be asked to answer about a case years afterwards.
            models.Index(
                fields=["decided_by", "-decided_at"], name="ix_review_approval_decided_by"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.state} of result {self.result_id}"
