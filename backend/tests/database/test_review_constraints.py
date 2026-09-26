"""The `review` tables as PostgreSQL holds them.

Three groups of test here are doing something beyond exercising a constraint.

`test_a_single_constraint_over_all_four_targets_would_not_work` demonstrates, in SQL, the failure
that the four partial unique constraints exist to avoid. It is the only test here that creates a
constraint of its own, and it does so because the alternative - a comment asserting that nulls are
distinct - is exactly the kind of claim that turns out to be wrong.

The reordering tests exercise the schema's only deferrable constraint, and they exercise it the way
the reordering service will: by leaving two rows at one position in the middle of a transaction.

The countersignature tests are the database half of. A control against self-approval implemented
in a service method is defeated by the second call site, so it is here instead.
"""

from __future__ import annotations

import pytest
from django.db import IntegrityError, connection, transaction
from django.db.models import ProtectedError
from django.utils import timezone

from apps.review.models import (
    MAX_NOTE_LENGTH,
    ApprovalState,
    Note,
    Rating,
    ResultOrder,
    Scope,
    TagType,
)
from tests.factories.accounts import ReviewerFactory, UserFactory
from tests.factories.cases import CaseFactory, ImageQueryFactory, ResultFactory, RunFactory
from tests.factories.review import (
    ApprovalFactory,
    CountersignedApprovalFactory,
    DecidedApprovalFactory,
    NoteFactory,
    RatingFactory,
    ResultOrderFactory,
    TagAssignmentFactory,
    TagFactory,
)

pytestmark = [pytest.mark.integration, pytest.mark.django_db]

SCOPED_FACTORIES = [RatingFactory, TagAssignmentFactory, NoteFactory]
SCOPED_IDS = ["rating", "tagassignment", "note"]


def constraint_names(table: str) -> set[str]:
    with connection.cursor() as cursor:
        cursor.execute("SELECT conname FROM pg_constraint WHERE conrelid = %s::regclass", [table])
        return {row[0] for row in cursor.fetchall()}


def index_names(table: str) -> set[str]:
    with connection.cursor() as cursor:
        cursor.execute("SELECT indexname FROM pg_indexes WHERE tablename = %s", [table])
        return {row[0] for row in cursor.fetchall()}


def target_for(scope: str) -> dict[str, object]:
    """One populated target for the given scope, so the scope tests can be parametrised over all
 four without four near-identical bodies."""
    return {
        Scope.CASE: lambda: {"case": CaseFactory(), "result": None},
        Scope.RUN: lambda: {"run": RunFactory(), "result": None},
        Scope.QUERY: lambda: {"query": ImageQueryFactory(), "result": None},
        Scope.RESULT: lambda: {"result": ResultFactory()},
    }[scope]


# ----------------------------------------------------------------------------------------
# The names exist.
# ----------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("table", "constraint"),
    [
        ("review_rating", "ck_review_rating_scope_target"),
        ("review_tagassignment", "ck_review_tagassignment_scope_target"),
        ("review_note", "ck_review_note_scope_target"),
    ],
)
def test_the_scope_constraint_exists_on_every_scoped_table(table: str, constraint: str) -> None:
    """Written out rather than composed from the table name, because `test_constraint_closure.py`
 finds the constraints a test exercises by reading these files for their names. A name assembled
 at run time is invisible to that search, and the constraint then counts as untested."""
    assert constraint in constraint_names(table)


@pytest.mark.parametrize(
    ("table", "expected"),
    [
        ("review_rating", {"ck_review_rating_value_range"}),
        ("review_tag", {"uq_review_tag_type_label", "ck_review_tag_type_valid"}),
        (
            "review_note",
            {
                "uq_review_note_public_id",
                "ck_review_note_body_length",
                "ck_review_note_supersession_paired",
                "ck_review_note_no_self_supersede",
            },
        ),
        (
            "review_resultorder",
            {"uq_review_resultorder_case_result", "uq_review_resultorder_case_position"},
        ),
        (
            "review_approval",
            {
                "uq_review_approval_result",
                "uq_review_approval_public_id",
                "ck_review_approval_state_valid",
                "ck_review_approval_decision_paired",
                "ck_review_approval_countersign_paired",
                "ck_review_approval_no_self_countersign",
                "ck_review_approval_countersign_requires_decision",
                "ck_review_approval_withdrawal_paired",
            },
        ),
    ],
)
def test_named_constraints_exist(table: str, expected: set[str]) -> None:
    assert expected <= constraint_names(table)


@pytest.mark.parametrize("scope", [s.value for s in Scope])
def test_there_is_one_partial_unique_index_per_scope(scope: str) -> None:
    """Four, not one. A single unique constraint over `(scope, case_id, run_id, query_id, result_id,
 author_id)` would permit unlimited duplicates, because PostgreSQL treats nulls as distinct and
 three of the four target columns are always null."""
    assert f"uq_review_rating_{scope}_author" in index_names("review_rating")
    assert f"uq_review_tagassignment_{scope}_tag" in index_names("review_tagassignment")


def test_a_single_constraint_over_all_four_targets_would_not_work() -> None:
    """The failure the four partial constraints avoid, demonstrated rather than asserted.

 A unique index over all four target columns plus the author admits two identical rows, because
 each contains three nulls and PostgreSQL considers nulls distinct. This is the standard way a
 discriminated table of this shape goes wrong, and it goes wrong silently.
 """
    with connection.cursor() as cursor:
        cursor.execute(
            """
 CREATE UNIQUE INDEX tmp_naive_scope_unique
 ON review_rating (case_id, run_id, query_id, result_id, author_id)
 """
        )

    author = ReviewerFactory()
    case = CaseFactory()
    RatingFactory(scope=Scope.CASE, case=case, result=None, author=author, value=3)

    # The naive index does not stop this. Only `uq_review_rating_case_author` does, so the write
    # below still fails - but on the partial constraint, which is the point.
    with pytest.raises(IntegrityError), transaction.atomic():
        RatingFactory(scope=Scope.CASE, case=case, result=None, author=author, value=5)

    with connection.cursor() as cursor:
        cursor.execute(
            """
 SELECT indisunique FROM pg_index
 WHERE indexrelid = 'tmp_naive_scope_unique'::regclass
 """
        )
        assert cursor.fetchone == (True,)


# ----------------------------------------------------------------------------------------
# The scope discriminator.
# ----------------------------------------------------------------------------------------


@pytest.mark.parametrize("factory", SCOPED_FACTORIES, ids=SCOPED_IDS)
@pytest.mark.parametrize("scope", [s.value for s in Scope])
def test_every_scope_accepts_its_own_target(factory: type, scope: str) -> None:
    row = factory(scope=scope, **target_for(scope))
    assert row.pk is not None


@pytest.mark.parametrize("factory", SCOPED_FACTORIES, ids=SCOPED_IDS)
@pytest.mark.parametrize("scope", [s.value for s in Scope])
def test_a_scope_with_no_target_is_refused(factory: type, scope: str) -> None:
    with pytest.raises(IntegrityError), transaction.atomic():
        factory(scope=scope, case=None, run=None, query=None, result=None)


@pytest.mark.parametrize("factory", SCOPED_FACTORIES, ids=SCOPED_IDS)
def test_a_scope_naming_one_target_while_another_is_populated_is_refused(factory: type) -> None:
    """The half a naive "at least one is set" constraint would miss. The discriminator has to agree
 with the populated column, or `target` resolves to null on a row that looks complete."""
    with pytest.raises(IntegrityError), transaction.atomic():
        factory(scope=Scope.CASE, case=None, result=ResultFactory())


@pytest.mark.parametrize("factory", SCOPED_FACTORIES, ids=SCOPED_IDS)
def test_two_targets_at_once_are_refused(factory: type) -> None:
    with pytest.raises(IntegrityError), transaction.atomic():
        factory(scope=Scope.RESULT, result=ResultFactory(), case=CaseFactory())


@pytest.mark.parametrize("factory", SCOPED_FACTORIES, ids=SCOPED_IDS)
@pytest.mark.parametrize("scope", [s.value for s in Scope])
def test_the_target_property_resolves_through_the_discriminator(factory: type, scope: str) -> None:
    """Rather than testing each column in turn, which is what a reader would otherwise do - in an
 order that would quietly become the precedence rule."""
    targets = target_for(scope)
    row = factory(scope=scope, **targets)

    expected = next(value for value in targets.values() if value is not None)
    assert row.target == expected


@pytest.mark.parametrize("factory", SCOPED_FACTORIES, ids=SCOPED_IDS)
def test_protect_refuses_to_delete_a_target_that_carries_review(factory: type) -> None:
    """Referential integrity is why this is four foreign keys and not a generic content-type
    reference. A generic reference cannot refuse this deletion."""
    row = factory()
    with pytest.raises(ProtectedError):
        row.result.delete()


# ----------------------------------------------------------------------------------------
# Ratings.
# ----------------------------------------------------------------------------------------


@pytest.mark.parametrize("value", [1, 3, 5])
def test_a_rating_within_one_to_five_is_accepted(value: int) -> None:
    assert RatingFactory(value=value).value == value


@pytest.mark.parametrize("value", [0, 6, -1, 100])
def test_a_rating_outside_one_to_five_is_refused(value: int) -> None:
    with pytest.raises(IntegrityError), transaction.atomic():
        RatingFactory(value=value)


def test_an_author_rates_a_target_once() -> None:
    first = RatingFactory()
    with pytest.raises(IntegrityError), transaction.atomic():
        RatingFactory(result=first.result, author=first.author)


def test_two_authors_rate_the_same_target() -> None:
    first = RatingFactory()
    RatingFactory(result=first.result, author=ReviewerFactory())
    assert Rating.objects.filter(result=first.result).count() == 2


def test_one_author_rates_two_targets() -> None:
    first = RatingFactory()
    second = RatingFactory(author=first.author)
    assert first.result_id != second.result_id


def test_the_same_pair_may_be_rated_at_two_scopes() -> None:
    """The partial constraints are per scope, so a reviewer rating both a run and one of its results
 is two rows and not a conflict."""
    author = ReviewerFactory()
    run = RunFactory()
    RatingFactory(scope=Scope.RUN, run=run, result=None, author=author)
    RatingFactory(scope=Scope.RESULT, result=ResultFactory(), author=author)

    assert Rating.objects.filter(author=author).count() == 2


# ----------------------------------------------------------------------------------------
# Tags.
# ----------------------------------------------------------------------------------------


def test_a_tag_label_is_unique_within_its_type() -> None:
    TagFactory(tag_type=TagType.PRIORITY, label="urgent")
    with pytest.raises(IntegrityError), transaction.atomic():
        TagFactory(tag_type=TagType.PRIORITY, label="urgent")


def test_the_same_label_may_exist_under_two_types() -> None:
    TagFactory(tag_type=TagType.PRIORITY, label="review")
    assert TagFactory(tag_type=TagType.STATUS, label="review").pk is not None


def test_an_unrecognised_tag_type_is_refused() -> None:
    with pytest.raises(IntegrityError), transaction.atomic():
        TagFactory(tag_type="severity")


def test_a_tag_is_applied_once_per_target() -> None:
    first = TagAssignmentFactory()
    with pytest.raises(IntegrityError), transaction.atomic():
        TagAssignmentFactory(result=first.result, tag=first.tag)


def test_two_tags_may_be_applied_to_one_target() -> None:
    first = TagAssignmentFactory()
    second = TagAssignmentFactory(result=first.result, tag=TagFactory())
    assert second.pk != first.pk


def test_the_vocabulary_is_separate_from_its_application() -> None:
    """Splitting these is what stops the label of a tag being edited on one target and not
 another: there is one row holding the label, and the assignments point at it."""
    tag = TagFactory(label="urgent")
    first = TagAssignmentFactory(tag=tag)
    second = TagAssignmentFactory(tag=tag)

    tag.label = "immediate"
    tag.save(update_fields=["label", "updated_at"])

    first.refresh_from_db()
    second.refresh_from_db()
    assert first.tag.label == second.tag.label == "immediate"


def test_protect_refuses_to_delete_an_applied_tag() -> None:
    assignment = TagAssignmentFactory()
    with pytest.raises(ProtectedError):
        assignment.tag.delete()


# ----------------------------------------------------------------------------------------
# Notes: amendment as a new record.
# ----------------------------------------------------------------------------------------


def test_two_notes_cannot_share_a_public_identifier() -> None:
    """and the constraint `review.0001` should have carried. A note is addressed by this
 identifier and cited by it in a report, so a duplicate would make one URL resolve to two notes
 and a citation ambiguous. The omission was found by `test_constraint_closure.py` rather than by
 reading the model, which is the reason that test exists."""
    first = NoteFactory()
    with pytest.raises(IntegrityError), transaction.atomic():
        NoteFactory(public_id=first.public_id)


def test_an_empty_note_is_refused() -> None:
    with pytest.raises(IntegrityError), transaction.atomic():
        NoteFactory(body="")


def test_a_note_beyond_the_length_limit_is_refused() -> None:
    with pytest.raises(IntegrityError), transaction.atomic():
        NoteFactory(body="a" * (MAX_NOTE_LENGTH + 1))


def test_a_note_at_the_length_limit_is_accepted() -> None:
    assert NoteFactory(body="a" * MAX_NOTE_LENGTH).pk is not None


def test_a_note_is_amended_by_writing_a_new_one() -> None:
    """The superseded note remains readable, which is the difference between a record
 of what was thought at the time and a record of what someone currently wants it to have been."""
    original = NoteFactory(body="The tread matches at the heel.")
    amendment = NoteFactory(
        result=original.result,
        author=original.author,
        body="The tread matches at the heel and toe.",
    )

    original.superseded_by = amendment
    original.superseded_at = timezone.now()
    original.save(update_fields=["superseded_by", "superseded_at", "updated_at"])

    original.refresh_from_db()
    assert not original.is_current
    assert amendment.is_current
    assert original.body == "The tread matches at the heel."
    assert Note.objects.filter(result=original.result).count() == 2


@pytest.mark.parametrize(
    ("with_note", "with_time"), [(True, False), (False, True)], ids=["note_only", "time_only"]
)
def test_supersession_and_its_timestamp_are_paired(with_note: bool, with_time: bool) -> None:
    other = NoteFactory()
    with pytest.raises(IntegrityError), transaction.atomic():
        NoteFactory(
            superseded_by=other if with_note else None,
            superseded_at=timezone.now() if with_time else None,
        )


def test_a_note_cannot_supersede_itself() -> None:
    note = NoteFactory()
    with pytest.raises(IntegrityError), transaction.atomic():
        Note.objects.filter(pk=note.pk).update(superseded_by=note.pk, superseded_at=timezone.now())


def test_notes_are_hierarchical() -> None:
    parent = NoteFactory()
    reply = NoteFactory(result=parent.result, parent=parent)
    assert list(parent.replies.all()) == [reply]


def test_protect_refuses_to_delete_a_note_with_replies() -> None:
    reply = NoteFactory(parent=NoteFactory())
    with pytest.raises(ProtectedError):
        reply.parent.delete()


def test_a_note_may_be_pinned() -> None:
    assert NoteFactory(scope=Scope.CASE, case=CaseFactory(), result=None, is_pinned=True).is_pinned


# ----------------------------------------------------------------------------------------
# Result ordering: the schema's only deferrable constraint.
# ----------------------------------------------------------------------------------------


def test_a_result_holds_one_position_in_a_case() -> None:
    first = ResultOrderFactory()
    with pytest.raises(IntegrityError), transaction.atomic():
        ResultOrderFactory(case=first.case, result=first.result, position=99)


def test_a_position_is_held_by_one_result() -> None:
    """Deferred, so the violation arrives at commit and not at the statement that caused it.

 The check is forced with `SET CONSTRAINTS ALL IMMEDIATE`, which is the mechanism a service can
 use to get the error at a point where it can still be handled. Without it the write appears to
 succeed and the failure surfaces during teardown, attached to no statement in particular - which
 is the price of the deferral and is worth seeing once in a test.
 """
    first = ResultOrderFactory(position=1)
    with pytest.raises(IntegrityError), transaction.atomic():
        ResultOrderFactory(case=first.case, position=1)
        with connection.cursor() as cursor:
            cursor.execute("SET CONSTRAINTS ALL IMMEDIATE")


def test_the_position_constraint_is_deferrable() -> None:
    with connection.cursor() as cursor:
        cursor.execute(
            """
 SELECT condeferrable, condeferred FROM pg_constraint
 WHERE conname = 'uq_review_resultorder_case_position'
 """
        )
        assert cursor.fetchone == (True, True)


def test_the_position_constraint_is_the_only_deferrable_one_in_the_schema() -> None:
    """Stated as a test because deferral is a real loosening: the check moves from the statement to
 the commit, and a constraint that is deferrable everywhere is a constraint that catches
 mistakes later and further from their cause."""
    with connection.cursor() as cursor:
        cursor.execute(
            """
 SELECT conname FROM pg_constraint c
 JOIN pg_class t ON t.oid = c.conrelid
 JOIN pg_namespace n ON n.oid = t.relnamespace
 WHERE c.condeferrable AND n.nspname = 'public' AND c.contype <> 'f'
 """
        )
        assert {row[0] for row in cursor.fetchall()} == {"uq_review_resultorder_case_position"}


def test_two_results_may_swap_positions_within_one_transaction() -> None:
    """What the deferral is for. Moving one result past another means two rows briefly claim the
 same position, whichever order the updates are issued in. Without deferral the service would
 have to shuffle through a position nobody uses, which is both slower and a state a concurrent
 reader could observe.
 """
    case = CaseFactory()
    first = ResultOrderFactory(case=case, position=1)
    second = ResultOrderFactory(case=case, position=2)

    with transaction.atomic():
        ResultOrder.objects.filter(pk=first.pk).update(position=2)
        ResultOrder.objects.filter(pk=second.pk).update(position=1)

    first.refresh_from_db()
    second.refresh_from_db()
    assert (first.position, second.position) == (2, 1)


def test_a_collision_left_unresolved_still_fails_at_commit() -> None:
    """Deferral moves the check; it does not remove it.

 The counterpart to the swap above. There, two updates resolved the collision before the check
 ran. Here only one is issued, and the constraint refuses it the moment the check is taken.
 """
    case = CaseFactory()
    first = ResultOrderFactory(case=case, position=1)
    ResultOrderFactory(case=case, position=2)

    with pytest.raises(IntegrityError), transaction.atomic():
        ResultOrder.objects.filter(pk=first.pk).update(position=2)
        with connection.cursor() as cursor:
            cursor.execute("SET CONSTRAINTS ALL IMMEDIATE")


# ----------------------------------------------------------------------------------------
# Approval: the evidential act.
# ----------------------------------------------------------------------------------------


def test_a_result_carries_one_approval() -> None:
    first = ApprovalFactory()
    with pytest.raises(IntegrityError), transaction.atomic():
        ApprovalFactory(result=first.result)


def test_a_requested_approval_has_no_decision_yet() -> None:
    approval = ApprovalFactory()
    assert approval.state == ApprovalState.REQUESTED
    assert approval.decided_by_id is None
    assert approval.decided_at is None


@pytest.mark.parametrize(
    "state",
    [ApprovalState.APPROVED, ApprovalState.REJECTED, ApprovalState.WITHDRAWN],
)
def test_any_state_past_requested_needs_a_decider_and_a_time(state: str) -> None:
    with pytest.raises(IntegrityError), transaction.atomic():
        ApprovalFactory(state=state)


def test_an_unrecognised_approval_state_is_refused() -> None:
    with pytest.raises(IntegrityError), transaction.atomic():
        ApprovalFactory(state="pending")


def test_the_decider_is_a_foreign_key_and_not_free_text() -> None:
    """A name in a text column is not an attribution: it cannot be verified, it does
 not follow the person's account when it is disabled, and two people with the same name are
 indistinguishable in it."""
    approval = DecidedApprovalFactory()
    assert approval.decided_by.username
    with pytest.raises(ProtectedError):
        approval.decided_by.delete()


def test_an_approval_may_be_countersigned_by_a_second_person() -> None:
    approval = CountersignedApprovalFactory()
    assert approval.countersigned_by_id != approval.decided_by_id


def test_self_countersignature_is_refused_by_the_database() -> None:
    """In the database rather than in a service method, because a control against
 self-approval implemented in application code is defeated by the second call site - and the
 second call site always arrives."""
    reviewer = ReviewerFactory()
    with pytest.raises(IntegrityError), transaction.atomic():
        CountersignedApprovalFactory(decided_by=reviewer, countersigned_by=reviewer)


@pytest.mark.parametrize(
    ("with_person", "with_time"), [(True, False), (False, True)], ids=["person_only", "time_only"]
)
def test_the_countersignature_and_its_timestamp_are_paired(
    with_person: bool, with_time: bool
) -> None:
    with pytest.raises(IntegrityError), transaction.atomic():
        DecidedApprovalFactory(
            countersigned_by=UserFactory if with_person else None,
            countersigned_at=timezone.now() if with_time else None,
        )


def test_a_countersignature_without_a_decision_is_refused() -> None:
    """There is nothing to endorse. A countersignature is not an independent decision - that is why
 it is columns on this row rather than a row of its own."""
    with pytest.raises(IntegrityError), transaction.atomic():
        ApprovalFactory(countersigned_by=UserFactory(), countersigned_at=timezone.now())


def test_withdrawal_is_a_transition_and_not_a_deletion() -> None:
    """The record of the earlier approval persists: a withdrawal says that a decision was
 made and later retracted, which is a different fact from no decision having been made."""
    approval = DecidedApprovalFactory()
    withdrawer = ReviewerFactory()
    approval.state = ApprovalState.WITHDRAWN
    approval.withdrawn_by = withdrawer
    approval.withdrawn_at = timezone.now()
    approval.save(update_fields=["state", "withdrawn_by", "withdrawn_at", "updated_at"])

    approval.refresh_from_db()
    assert approval.state == ApprovalState.WITHDRAWN
    assert approval.decided_by_id is not None
    assert approval.decided_at is not None
    assert approval.withdrawn_by_id == withdrawer.pk


@pytest.mark.parametrize(
    ("with_person", "with_time"), [(True, False), (False, True)], ids=["person_only", "time_only"]
)
def test_the_withdrawal_and_its_timestamp_are_paired(with_person: bool, with_time: bool) -> None:
    with pytest.raises(IntegrityError), transaction.atomic():
        DecidedApprovalFactory(
            withdrawn_by=UserFactory if with_person else None,
            withdrawn_at=timezone.now() if with_time else None,
        )
