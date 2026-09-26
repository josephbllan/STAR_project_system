"""Case scoping, the primary authorisation control.

Every assertion here is about a *refusal expressed as absence*. A caller outside a case does not
receive a row it may not read, and does not receive an error telling it one exists - the two being
the same requirement seen from either side.

The suite is organised around the twelve scoped models rather than around the rule, because the rule
is one method and the twelve declarations of `case_paths` are where it can go wrong. A path that
traverses the wrong field still returns rows; it just returns the wrong ones, and nothing but a test
against a second case would notice.
"""

from __future__ import annotations

import pytest

from apps.cases.models import Case, CaseMembership, Query, Result, Run, RunCorpus
from apps.common.querysets import ScopedQuerySet
from apps.datasets.models import EvidenceFile
from apps.reporting.models import Report
from apps.review.models import Approval, Note, Rating, ResultOrder, Scope, TagAssignment
from tests import scenarios
from tests.factories.accounts import AdministratorFactory, AuditorFactory, UserFactory
from tests.factories.cases import CaseMembershipFactory, RunCorpusFactory
from tests.factories.datasets import CorpusFactory, EvidenceFileFactory
from tests.factories.reporting import ReportFactory
from tests.factories.review import (
    NoteFactory,
    RatingFactory,
    ResultOrderFactory,
    TagAssignmentFactory,
)

pytestmark = [pytest.mark.integration, pytest.mark.django_db]


# ----------------------------------------------------------------------------------------
# The rule itself.
# ----------------------------------------------------------------------------------------


def test_the_owner_of_a_case_sees_it() -> None:
    team = scenarios.case_team
    assert list(Case.objects.visible_to(team.owner)) == [team.case]


def test_a_member_of_a_case_sees_it() -> None:
    team = scenarios.case_team
    assert list(Case.objects.visible_to(team.reader)) == [team.case]


def test_an_outsider_sees_nothing() -> None:
    """Access to one case confers no access to another, and the outsider here holds the same
 role as the owner - so what is being tested is the membership and not the role."""
    team = scenarios.case_team
    assert not Case.objects.visible_to(team.outsider).exists()


def test_a_revoked_member_stops_seeing_the_case() -> None:
    """The condition most easily left out of a reimplementation of this rule. A membership is
 revoked by setting a timestamp, never by deletion, so a filter omitting
 `revoked_at IS NULL` restores access to everyone whose access was ever taken away."""
    team = scenarios.case_team
    membership = CaseMembership.objects.get(case=team.case, user=team.reader)

    from django.utils import timezone

    membership.revoked_at = timezone.now()
    membership.revoked_by = team.owner
    membership.save(update_fields=["revoked_at", "revoked_by"])

    assert not Case.objects.visible_to(team.reader).exists()


def test_scoping_does_not_duplicate_a_case_the_caller_both_owns_and_belongs_to() -> None:
    """The join to memberships multiplies rows, so `visible_to` is distinct. Without it an owner who
 is also a member would see their case twice and a paginated count would be wrong."""
    team = scenarios.case_team
    CaseMembershipFactory(case=team.case, user=team.owner, granted_by=team.owner)

    assert Case.objects.visible_to(team.owner).count() == 1


def test_two_memberships_on_two_cases_yield_two_cases() -> None:
    first = scenarios.case_team
    second = scenarios.case_team
    CaseMembershipFactory(case=second.case, user=first.reader, granted_by=second.owner)

    assert set(Case.objects.visible_to(first.reader)) == {first.case, second.case}


def test_an_administrator_sees_every_case_without_a_membership() -> None:
    """and the only exemption from the rule this module exists to enforce. Asserted by name
 so that the hole is documented by a test rather than discovered in the queryset, and so that
 removing it later fails here rather than silently narrowing what an administrator can reach."""
    first = scenarios.case_team
    second = scenarios.case_team

    assert set(Case.objects.visible_to(AdministratorFactory())) == {first.case, second.case}


def test_the_administrator_exemption_does_not_survive_deactivation() -> None:
    """The ordering of the checks in `visible_to`, which is the part of that method most easily
 rearranged by someone tidying it. A deactivated administrator must see nothing: if the exemption
 were evaluated before the active-account check, disabling the account would take away its
 password and leave its visibility."""
    scenarios.case_team
    administrator = AdministratorFactory(is_active=False)

    assert not Case.objects.visible_to(administrator).exists()


def test_the_administrator_exemption_reaches_transitively_scoped_models() -> None:
    """The exemption is applied once, in `visible_to`, so it holds for every model that uses it
 rather than for cases alone. A per-view role check would have had to be repeated twelve times,
 and the twelfth is the one that would have been forgotten."""
    team = scenarios.case_team
    run = scenarios.completed_run(case=team.case, created_by=team.owner)
    administrator = AdministratorFactory()
    assert set(Run.objects.visible_to(administrator)) == {run.run}
    assert set(Result.objects.visible_to(administrator)) == set(run.results)


def test_an_auditor_sees_no_case_at_all() -> None:
    """Expressed as an empty scope and not as absent routes: a route that exists and
 returns nothing can be tested, whereas a route never registered can only be asserted by its
 absence from a configuration someone may later add it to."""
    team = scenarios.case_team
    auditor = AuditorFactory()
    CaseMembershipFactory(case=team.case, user=auditor, granted_by=team.owner)

    assert not Case.objects.visible_to(auditor).exists()


def test_an_inactive_account_sees_nothing() -> None:
    """A deactivated account keeps its memberships, because the record of who had access is
 evidential. It must not keep the access."""
    team = scenarios.case_team
    team.reader.is_active = False
    team.reader.save(update_fields=["is_active"])

    assert not Case.objects.visible_to(team.reader).exists()


def test_an_anonymous_caller_sees_nothing() -> None:
    from django.contrib.auth.models import AnonymousUser

    scenarios.case_team
    assert not Case.objects.visible_to(AnonymousUser()).exists()


def test_no_user_at_all_sees_nothing() -> None:
    """`visible_to(None)` returns the empty set rather than raising. A view that has not resolved
 its user should produce no rows, not a 500 - and certainly not all rows."""
    scenarios.case_team
    assert not Case.objects.visible_to(None).exists()


def test_every_refusal_is_the_same_empty_set() -> None:
    """The property rests on. An outsider, an auditor, an inactive account and an anonymous
 caller must be indistinguishable from each other and from a caller asking about a case that does
 not exist."""
    from django.contrib.auth.models import AnonymousUser

    team = scenarios.case_team
    team.reader.is_active = False
    team.reader.save(update_fields=["is_active"])

    for caller in (team.outsider, AuditorFactory(), team.reader, AnonymousUser(), None):
        assert list(Case.objects.visible_to(caller)) == []


# ----------------------------------------------------------------------------------------
# Each scoped model reaches its case.
# ----------------------------------------------------------------------------------------


def test_a_run_is_scoped_through_its_case() -> None:
    mine = scenarios.completed_run
    theirs = scenarios.completed_run

    assert list(Run.objects.visible_to(mine.run.created_by)) == [mine.run]
    assert theirs.run not in set(Run.objects.visible_to(mine.run.created_by))


def test_a_query_is_scoped_through_its_run() -> None:
    mine = scenarios.completed_run
    scenarios.completed_run

    assert list(Query.objects.visible_to(mine.run.created_by)) == [mine.query]


def test_a_result_is_scoped_through_three_joins() -> None:
    """The deepest scoped path in the schema, and the one that matters most: a result names an
 evidence file, so an unscoped result collection discloses which evidence appears in another
 investigator's case."""
    mine = scenarios.completed_run(result_count=3)
    theirs = scenarios.completed_run(result_count=3)

    visible = set(Result.objects.visible_to(mine.run.created_by))
    assert visible == set(mine.results)
    assert visible.isdisjoint(theirs.results)


def test_a_run_corpus_restriction_is_scoped() -> None:
    mine = scenarios.completed_run
    theirs = scenarios.completed_run
    RunCorpusFactory(run=theirs.run, corpus=CorpusFactory())

    visible = RunCorpus.objects.visible_to(mine.run.created_by)
    assert {row.run_id for row in visible} == {mine.run.pk}


def test_a_membership_row_is_scoped() -> None:
    """So that a case's member list is served by the same rule as everything else, rather than by a
 second one that might disagree with it."""
    mine = scenarios.case_team
    scenarios.case_team

    visible = CaseMembership.objects.visible_to(mine.owner)
    assert {row.case_id for row in visible} == {mine.case.pk}


def test_a_report_is_scoped_through_its_case() -> None:
    mine = scenarios.case_team
    theirs = scenarios.case_team
    ReportFactory(case=mine.case, requested_by=mine.owner)
    ReportFactory(case=theirs.case, requested_by=theirs.owner)

    visible = Report.objects.visible_to(mine.owner)
    assert {report.case_id for report in visible} == {mine.case.pk}


def test_a_report_requested_by_the_caller_on_another_case_is_still_hidden() -> None:
    """The scope is the case, not the requester. A report is derived from evidence, so access to it
 follows access to the case rather than authorship of the request."""
    theirs = scenarios.case_team
    outsider = theirs.outsider
    ReportFactory(case=theirs.case, requested_by=outsider)

    assert not Report.objects.visible_to(outsider).exists()


def test_a_result_ordering_is_scoped() -> None:
    mine = scenarios.completed_run(result_count=1)
    theirs = scenarios.completed_run(result_count=1)
    ResultOrderFactory(case=mine.run.case, result=mine.top, ordered_by=mine.run.created_by)
    ResultOrderFactory(case=theirs.run.case, result=theirs.top, ordered_by=theirs.run.created_by)

    visible = ResultOrder.objects.visible_to(mine.run.created_by)
    assert {row.case_id for row in visible} == {mine.run.case_id}


def test_an_approval_is_scoped_through_its_result() -> None:
    mine = scenarios.reviewed_result
    scenarios.reviewed_result
    owner = mine.result.query.run.case.owner

    assert list(Approval.objects.visible_to(owner)) == [mine.approval]


# ----------------------------------------------------------------------------------------
# The four-target review models.
# ----------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("model", "factory"),
    [(Rating, RatingFactory), (TagAssignment, TagAssignmentFactory), (Note, NoteFactory)],
    ids=["rating", "tag_assignment", "note"],
)
@pytest.mark.parametrize("scope", [s.value for s in Scope])
def test_a_scoped_review_record_reaches_its_case_by_whichever_column_is_populated(
    model: type, factory: type, scope: str
) -> None:
    """times three models, times four scopes. Three of the four target columns are null on
 any given row, so the case is reached through whichever one is not - and a `case_paths`
 declaration omitting one scope would hide every record written at that scope while looking
 correct for the other three."""
    mine = scenarios.completed_run(result_count=1)
    owner = mine.run.case.owner
    targets = {
        Scope.CASE: {"case": mine.run.case, "result": None},
        Scope.RUN: {"run": mine.run, "result": None},
        Scope.QUERY: {"query": mine.query, "result": None},
        Scope.RESULT: {"result": mine.top},
    }[scope]

    record = factory(scope=scope, **targets)
    assert list(model.objects.visible_to(owner)) == [record]


@pytest.mark.parametrize("scope", [s.value for s in Scope])
def test_a_scoped_review_record_is_hidden_from_an_outsider_at_every_scope(scope: str) -> None:
    theirs = scenarios.completed_run(result_count=1)
    targets = {
        Scope.CASE: {"case": theirs.run.case, "result": None},
        Scope.RUN: {"run": theirs.run, "result": None},
        Scope.QUERY: {"query": theirs.query, "result": None},
        Scope.RESULT: {"result": theirs.top},
    }[scope]
    NoteFactory(scope=scope, **targets)

    assert not Note.objects.visible_to(UserFactory()).exists()


def test_a_note_on_one_case_does_not_leak_through_a_second_case() -> None:
    """The failure a single-case test cannot see: a `case_paths` entry traversing the wrong field
 still returns rows, just the wrong ones."""
    mine = scenarios.completed_run(result_count=1)
    theirs = scenarios.completed_run(result_count=1)
    mine_note = NoteFactory(scope=Scope.RESULT, result=mine.top)
    NoteFactory(scope=Scope.RESULT, result=theirs.top)

    assert list(Note.objects.visible_to(mine.run.case.owner)) == [mine_note]


# ----------------------------------------------------------------------------------------
# Shared evidence: the one exception, and why it is narrow.
# ----------------------------------------------------------------------------------------


def test_shared_corpus_evidence_is_visible_to_any_authenticated_account() -> None:
    """The one model where a null case means *shared* rather than orphaned. Without
 the exception no account could search the shared corpora at all, which is what the system is
 for."""
    EvidenceFileFactory(case=None)
    assert EvidenceFile.objects.visible_to(UserFactory()).count() == 1


def test_case_scoped_evidence_is_not_visible_outside_its_case() -> None:
    team = scenarios.case_team
    EvidenceFileFactory(case=team.case)

    assert EvidenceFile.objects.visible_to(team.owner).count() == 1
    assert not EvidenceFile.objects.visible_to(team.outsider).exists()


def test_a_member_sees_both_the_shared_and_the_case_scoped_evidence() -> None:
    team = scenarios.case_team
    EvidenceFileFactory(case=None)
    EvidenceFileFactory(case=team.case)

    assert EvidenceFile.objects.visible_to(team.reader).count() == 2


def test_an_auditor_sees_no_evidence_even_though_some_is_shared() -> None:
    """The role check precedes the shared-row exception, and that ordering is the point: says
 the auditor reads audit records and nothing else, so "shared" must not mean "shared with
 everyone including the role that may read nothing"."""
    EvidenceFileFactory(case=None)
    assert not EvidenceFile.objects.visible_to(AuditorFactory()).exists()


def test_the_shared_exception_applies_to_exactly_one_model() -> None:
    """Asserted by enumeration, because the exception is a hole in the control and a second one
 added casually would not announce itself."""
    scoped = [
        Case,
        CaseMembership,
        Run,
        RunCorpus,
        Query,
        Result,
        Rating,
        TagAssignment,
        Note,
        ResultOrder,
        Approval,
        Report,
        EvidenceFile,
    ]
    sharing = [
        model
        for model in scoped
        if model.objects.get_queryset.shared_when_unscoped  # type: ignore[attr-defined]
    ]
    assert sharing == [EvidenceFile]


# ----------------------------------------------------------------------------------------
# The kernel refuses to guess.
# ----------------------------------------------------------------------------------------


def test_a_queryset_with_no_declared_path_refuses_rather_than_returning_everything() -> None:
    """The alternative default - treat an undeclared model as unscoped - would mean that forgetting
 the declaration produced a working, unscoped endpoint. Refusing produces a loud failure on the
 first request instead."""

    class Undeclared(ScopedQuerySet):
        pass

    with pytest.raises(TypeError, match="sets no case_paths"):
        Undeclared(model=Case).visible_to(UserFactory())


def test_every_scoped_model_declares_a_path() -> None:
    for model in (Case, CaseMembership, Run, RunCorpus, Query, Result, Report, EvidenceFile):
        assert model.objects.get_queryset.case_paths is not None, model  # type: ignore[attr-defined]
