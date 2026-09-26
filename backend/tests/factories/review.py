"""Review and approval.

The scoped factories default to the `result` scope and set the discriminator from the target, so a
test that passes `case=...` gets a case-scoped row without also having to remember to say
`scope="case"` - a pairing the check constraint would otherwise reject, and reject correctly.
"""

from __future__ import annotations

from uuid import uuid4

import factory
from django.utils import timezone

from apps.review.models import (
    Approval,
    ApprovalState,
    Note,
    Rating,
    ResultOrder,
    Scope,
    Tag,
    TagAssignment,
    TagType,
)
from tests.factories.accounts import InvestigatorFactory, ReviewerFactory, UserFactory
from tests.factories.cases import CaseFactory, ResultFactory


class _ScopedFactory(factory.django.DjangoModelFactory):
    """Derives `scope` from whichever target was supplied.

 Without this every scoped test would state the pairing twice, and the second statement is the
 one that goes stale. The default is a result, which is the scope every review feature uses
 first.
 """

    class Meta:
        abstract = True

    case = None
    run = None
    query = None
    result = factory.SubFactory(ResultFactory)
    scope = factory.LazyAttribute(
        lambda o: (
            Scope.CASE
            if o.case
            else Scope.RUN
            if o.run
            else Scope.QUERY
            if o.query
            else Scope.RESULT
        )
    )


class RatingFactory(_ScopedFactory):
    class Meta:
        model = Rating

    value = 4
    author = factory.SubFactory(ReviewerFactory)


class TagFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Tag

    tag_type = TagType.PRIORITY
    label = factory.Sequence(lambda n: f"priority-{n}")
    colour = "#336699"
    is_active = True


class TagAssignmentFactory(_ScopedFactory):
    class Meta:
        model = TagAssignment

    tag = factory.SubFactory(TagFactory)
    assigned_by = factory.SubFactory(ReviewerFactory)


class NoteFactory(_ScopedFactory):
    class Meta:
        model = Note

    public_id = factory.LazyFunction(uuid4)
    parent = None
    #: Already sanitised, because the sanitised form is the only form stored. A factory
    #: producing raw markup would let a test assert behaviour the system never sees.
    body = "The tread pattern matches at the heel."
    is_pinned = False
    author = factory.SubFactory(ReviewerFactory)
    superseded_by = None
    superseded_at = None


class ResultOrderFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = ResultOrder

    case = factory.SubFactory(CaseFactory)
    result = factory.SubFactory(ResultFactory)
    position = factory.Sequence(lambda n: n + 1)
    ordered_by = factory.SubFactory(InvestigatorFactory)


class ApprovalFactory(factory.django.DjangoModelFactory):
    """Defaults to `requested`, which is the only state in which the decision columns may be null.
 A factory defaulting to `approved` would have had to invent a decider, and every test would then
 have inherited an attribution nobody chose."""

    class Meta:
        model = Approval

    public_id = factory.LazyFunction(uuid4)
    result = factory.SubFactory(ResultFactory)
    state = ApprovalState.REQUESTED
    evidence_label = ""
    notes = ""
    requested_by = factory.SubFactory(InvestigatorFactory)
    decided_by = None
    decided_at = None
    countersigned_by = None
    countersigned_at = None
    withdrawn_by = None
    withdrawn_at = None


class DecidedApprovalFactory(ApprovalFactory):
    state = ApprovalState.APPROVED
    decided_by = factory.SubFactory(ReviewerFactory)
    decided_at = factory.LazyFunction(timezone.now)


class CountersignedApprovalFactory(DecidedApprovalFactory):
    state = ApprovalState.COUNTERSIGNED
    #: A different person from `decided_by`, which is what
    #: `ck_review_approval_no_self_countersign` requires.
    countersigned_by = factory.SubFactory(UserFactory)
    countersigned_at = factory.LazyFunction(timezone.now)
