"""The `cases` tables as PostgreSQL holds them.

The constraints here carry more of the system's meaning than those in any other app:

* `uq_cases_case_owner_name` scopes uniqueness to the owner, so a timestamp suffix is not
 needed in the case name.
* `ck_cases_query_type_payload` replaces the `TEXT_QUERY::` sentinel prefix.
* `uq_cases_result_query_file` performs deduplication in the database rather than in
 post-processing, which matters because over-fetching across two encoders and fusing is
 exactly the process that produces duplicates.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from django.db import IntegrityError, connection, transaction
from django.db.models import ProtectedError
from django.utils import timezone

from apps.cases.models import (
    MAX_QUERY_TEXT_LENGTH,
    MAX_TOP_K,
    AccessLevel,
    CaseMembership,
    CaseStatus,
    Query,
    QueryStatus,
    QueryType,
    Result,
    Run,
    RunStatus,
)
from tests.factories.accounts import AdministratorFactory, InvestigatorFactory, UserFactory
from tests.factories.cases import (
    CaseFactory,
    CaseMembershipFactory,
    ClosedCaseFactory,
    ImageQueryFactory,
    ResultFactory,
    RevokedCaseMembershipFactory,
    RunCorpusFactory,
    RunFactory,
    TextQueryFactory,
)
from tests.factories.datasets import ContentObjectFactory, CorpusFactory, EvidenceFileFactory
from tests.factories.search import Dinov2EncoderFactory, EncoderFactory
from tests.factories.tasks import TaskRunFactory

pytestmark = [pytest.mark.integration, pytest.mark.django_db]


def constraint_names(table: str) -> set[str]:
    with connection.cursor() as cursor:
        cursor.execute("SELECT conname FROM pg_constraint WHERE conrelid = %s::regclass", [table])
        return {row[0] for row in cursor.fetchall()}


def index_names(table: str) -> set[str]:
    with connection.cursor() as cursor:
        cursor.execute("SELECT indexname FROM pg_indexes WHERE tablename = %s", [table])
        return {row[0] for row in cursor.fetchall()}


# ----------------------------------------------------------------------------------------
# The names exist.
# ----------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("table", "expected"),
    [
        (
            "cases_case",
            {
                "uq_cases_case_owner_name",
                "uq_cases_case_public_id",
                "ck_cases_case_status_valid",
                "ck_cases_case_closed_at_paired",
            },
        ),
        (
            "cases_casemembership",
            {
                "ck_cases_casemembership_access_valid",
                "ck_cases_casemembership_revocation_pair",
            },
        ),
        (
            "cases_run",
            {
                "uq_cases_run_case_label",
                "uq_cases_run_public_id",
                "ck_cases_run_weights_range",
                "ck_cases_run_top_k_range",
                "ck_cases_run_overfetch_sane",
                "ck_cases_run_status_valid",
                "ck_cases_run_encoder_present",
            },
        ),
        ("cases_runcorpus", {"uq_cases_runcorpus_run_corpus"}),
        (
            "cases_query",
            {
                "uq_cases_query_run_sequence",
                "uq_cases_query_public_id",
                "ck_cases_query_type_payload",
                "ck_cases_query_text_length",
                "ck_cases_query_status_valid",
            },
        ),
        (
            "cases_result",
            {
                "uq_cases_result_query_rank",
                "uq_cases_result_query_file",
                "uq_cases_result_public_id",
                "ck_cases_result_rank_positive",
                "ck_cases_result_scores_range",
                "ck_cases_result_at_least_one_model_score",
            },
        ),
    ],
)
def test_named_constraints_exist(table: str, expected: set[str]) -> None:
    assert expected <= constraint_names(table)


def test_the_active_membership_uniqueness_is_a_partial_index() -> None:
    """`unique_together` cannot express a condition, so this is a partial unique index and appears
 in `pg_indexes` rather than in `pg_constraint`. Asserted separately so its absence from
 the constraint list above reads as intended."""
    assert "uq_cases_casemembership_active" in index_names("cases_casemembership")


@pytest.mark.parametrize(
    ("table", "column"),
    [
        ("cases_case", "owner_id"),
        ("cases_casemembership", "user_id"),
        ("cases_run", "case_id"),
        ("cases_query", "run_id"),
        ("cases_result", "query_id"),
        ("cases_result", "evidence_file_id"),
        ("cases_runcorpus", "run_id"),
    ],
)
def test_no_redundant_foreign_key_index_was_left_behind(table: str, column: str) -> None:
    leftover = {name for name in index_names(table) if name.startswith(f"{table}_{column}_")}
    assert leftover == set, f"{table}.{column} still carries an implicit index"


def test_the_access_history_index_exists() -> None:
    """Added during implementation and recorded in 3.2. Every other index on this
 table is partial on `revoked_at IS NULL`, which serves authorisation only; the access-history
 panel reads revoked rows by definition and had no index at all."""
    assert "ix_cases_casemembership_case" in index_names("cases_casemembership")


# ----------------------------------------------------------------------------------------
# Case identity.
# ----------------------------------------------------------------------------------------


def test_a_case_name_is_unique_within_its_owner() -> None:
    owner = InvestigatorFactory()
    CaseFactory(owner=owner, name="Operation Harrier")
    with pytest.raises(IntegrityError), transaction.atomic():
        CaseFactory(owner=owner, name="Operation Harrier")


def test_two_owners_may_use_the_same_case_name() -> None:
    """Uniqueness is scoped to the owner, so the same display name may be reused."""
    first = CaseFactory(owner=InvestigatorFactory(), name="Operation Harrier")
    second = CaseFactory(owner=InvestigatorFactory(), name="Operation Harrier")

    assert first.pk != second.pk


def test_an_unrecognised_case_status_is_refused() -> None:
    with pytest.raises(IntegrityError), transaction.atomic():
        CaseFactory(status="archived")


def test_a_closed_case_records_when_it_closed() -> None:
    case = ClosedCaseFactory()
    assert case.status == CaseStatus.CLOSED
    assert case.closed_at is not None


@pytest.mark.parametrize(
    ("status", "closed_at"),
    [(CaseStatus.CLOSED, None), (CaseStatus.OPEN, "now"), (CaseStatus.UNDER_REVIEW, "now")],
    ids=["closed_without_time", "open_with_time", "under_review_with_time"],
)
def test_the_closing_time_is_paired_with_the_closed_status(
    status: str, closed_at: str | None
) -> None:
    """Both directions. A closed case with no closing time cannot be reported from, and a
 closing time on an open case is a contradiction that some later query will believe."""
    with pytest.raises(IntegrityError), transaction.atomic():
        CaseFactory(status=status, closed_at=timezone.now() if closed_at else None)


def test_protect_refuses_to_delete_the_owner_of_a_case() -> None:
    case = CaseFactory()
    with pytest.raises(ProtectedError):
        case.owner.delete()


# ----------------------------------------------------------------------------------------
# Membership: revocation is a state.
# ----------------------------------------------------------------------------------------


def test_only_one_active_membership_per_case_and_user() -> None:
    first = CaseMembershipFactory()
    with pytest.raises(IntegrityError), transaction.atomic():
        CaseMembershipFactory(case=first.case, user=first.user)


def test_access_may_be_granted_revoked_and_granted_again() -> None:
    """The point of the partial index. Three rows, one live grant - and the two revoked rows are
 the record of who held access when, which a deleted row would not be."""
    case = CaseFactory()
    user = UserFactory()
    RevokedCaseMembershipFactory(case=case, user=user, access_level=AccessLevel.READ)
    RevokedCaseMembershipFactory(case=case, user=user, access_level=AccessLevel.CONTRIBUTE)
    live = CaseMembershipFactory(case=case, user=user, access_level=AccessLevel.REVIEW)

    assert CaseMembership.objects.filter(case=case, user=user).count() == 3
    active = CaseMembership.objects.filter(case=case, user=user, revoked_at__isnull=True)
    assert list(active) == [live]


def test_two_revoked_memberships_do_not_collide() -> None:
    """Directly, because this is what a non-partial unique constraint would have broken, and it
 would have broken it only on the second revocation - long after the wrong choice was made."""
    case = CaseFactory()
    user = UserFactory()
    first = RevokedCaseMembershipFactory(case=case, user=user)
    second = RevokedCaseMembershipFactory(case=case, user=user)

    assert first.pk != second.pk


@pytest.mark.parametrize("level", [a.value for a in AccessLevel])
def test_every_access_level_is_accepted(level: str) -> None:
    assert CaseMembershipFactory(access_level=level).pk is not None


def test_an_unrecognised_access_level_is_refused() -> None:
    with pytest.raises(IntegrityError), transaction.atomic():
        CaseMembershipFactory(access_level="admin")


@pytest.mark.parametrize(
    ("revoked_at", "with_actor"),
    [("now", False), (None, True)],
    ids=["revoked_without_actor", "actor_without_revocation"],
)
def test_a_revocation_and_its_actor_are_paired(revoked_at: str | None, with_actor: bool) -> None:
    with pytest.raises(IntegrityError), transaction.atomic():
        CaseMembershipFactory(
            revoked_at=timezone.now() if revoked_at else None,
            revoked_by=AdministratorFactory if with_actor else None,
        )


def test_the_grantor_is_recorded_and_protected() -> None:
    """Who granted access is part of the access trail, so the grantor's account cannot be removed
 while the grant stands."""
    grantor = AdministratorFactory()
    CaseMembershipFactory(granted_by=grantor)
    with pytest.raises(ProtectedError):
        grantor.delete()


def test_membership_reports_whether_it_is_active() -> None:
    assert CaseMembershipFactory.is_active
    assert not RevokedCaseMembershipFactory.is_active


# ----------------------------------------------------------------------------------------
# Runs.
# ----------------------------------------------------------------------------------------


def test_a_run_label_is_unique_within_its_case() -> None:
    first = RunFactory()
    with pytest.raises(IntegrityError), transaction.atomic():
        RunFactory(case=first.case, label=first.label)


def test_the_same_run_label_may_be_used_in_another_case() -> None:
    first = RunFactory(label="baseline")
    second = RunFactory(label="baseline")
    assert first.case_id != second.case_id


@pytest.mark.parametrize(
    ("model_weight", "metadata_weight"),
    [
        (Decimal("-0.001"), Decimal("0.300")),
        (Decimal("1.001"), Decimal("0.300")),
        (Decimal("0.500"), Decimal("-0.001")),
        (Decimal("0.500"), Decimal("1.001")),
    ],
    ids=["model_below", "model_above", "metadata_below", "metadata_above"],
)
def test_a_fusion_weight_outside_zero_to_one_is_refused(
    model_weight: Decimal, metadata_weight: Decimal
) -> None:
    """The fusion expression is meaningless outside this range: a weight above one amplifies one
 component beyond the score it is supposed to be a share of."""
    with pytest.raises(IntegrityError), transaction.atomic():
        RunFactory(model_weight=model_weight, metadata_weight=metadata_weight)


@pytest.mark.parametrize("weight", [Decimal("0.000"), Decimal("1.000")])
def test_a_fusion_weight_at_the_bound_is_accepted(weight: Decimal) -> None:
    """Both ends are legitimate configurations: zero means the component is ignored, one means it
 is the whole score. Refusing either would forbid the two clearest experiments a reviewer might
 want to run."""
    assert RunFactory(model_weight=weight, metadata_weight=weight).pk is not None


def test_the_weights_round_trip_exactly() -> None:
    """`numeric(4,3)` and not `double precision`, because these are configured values a reviewer
 filters and compares on. A score may be approximate; the weight that produced it may not."""
    run = RunFactory(model_weight=Decimal("0.375"), metadata_weight=Decimal("0.125"))
    run.refresh_from_db()

    assert run.model_weight == Decimal("0.375")
    assert run.metadata_weight == Decimal("0.125")


@pytest.mark.parametrize("top_k", [0, -1, MAX_TOP_K + 1])
def test_a_top_k_outside_its_range_is_refused(top_k: int) -> None:
    with pytest.raises(IntegrityError), transaction.atomic():
        RunFactory(top_k=top_k)


@pytest.mark.parametrize("top_k", [1, MAX_TOP_K])
def test_a_top_k_at_the_bound_is_accepted(top_k: int) -> None:
    assert RunFactory(top_k=top_k).pk is not None


@pytest.mark.parametrize(
    ("factor", "ceiling"),
    [(0, 500), (21, 500), (6, 0), (6, 5001)],
    ids=["factor_below", "factor_above", "ceiling_below", "ceiling_above"],
)
def test_an_insane_overfetch_is_refused(factor: int, ceiling: int) -> None:
    with pytest.raises(IntegrityError), transaction.atomic():
        RunFactory(overfetch_factor=factor, overfetch_ceiling=ceiling)


def test_the_overfetch_defaults_are_the_transferred_ones() -> None:
    """Over-fetch, discard registrations outside the run's corpora and those whose file is
 missing, then truncate. The alternative - filtering inside the approximate search - is not
 something HNSW supports without losing the recall the index exists to provide."""
    run = RunFactory()
    assert (run.overfetch_factor, run.overfetch_ceiling) == (6, 500)


def test_a_run_with_no_encoder_is_refused() -> None:
    """A run with no encoder cannot produce a score, so it is not an empty run - it is a
 row that will fail in a worker with nothing to point at."""
    with pytest.raises(IntegrityError), transaction.atomic():
        RunFactory(clip_encoder=None, dinov2_encoder=None)


@pytest.mark.parametrize(
    ("clip", "dinov2"), [(True, False), (False, True), (True, True)], ids=["clip", "dinov2", "both"]
)
def test_one_encoder_is_enough_and_two_are_permitted(clip: bool, dinov2: bool) -> None:
    run = RunFactory(
        clip_encoder=EncoderFactory if clip else None,
        dinov2_encoder=Dinov2EncoderFactory if dinov2 else None,
    )
    assert run.pk is not None


def test_an_unrecognised_run_status_is_refused() -> None:
    with pytest.raises(IntegrityError), transaction.atomic():
        RunFactory(status="paused")


@pytest.mark.parametrize("status", [s.value for s in RunStatus])
def test_every_run_status_is_accepted(status: str) -> None:
    assert RunFactory(status=status).pk is not None


def test_a_run_records_the_task_run_that_executed_it() -> None:
    task = TaskRunFactory(task_name="search.execute_run")
    assert RunFactory(task_run=task).task_run_id == task.pk


def test_protect_refuses_to_delete_an_encoder_a_run_used() -> None:
    """in the other direction: the weights are recorded on the run, and the encoder that
 produced the scores has to outlive the run for those scores to remain explicable."""
    run = RunFactory()
    with pytest.raises(ProtectedError):
        run.clip_encoder.delete()


# ----------------------------------------------------------------------------------------
# Corpus restriction.
# ----------------------------------------------------------------------------------------


def test_a_corpus_is_selected_once_per_run() -> None:
    first = RunCorpusFactory()
    with pytest.raises(IntegrityError), transaction.atomic():
        RunCorpusFactory(run=first.run, corpus=first.corpus)


def test_a_run_may_be_restricted_to_several_corpora() -> None:
    """A predicate joined in the same query as the metadata, rather than a choice of
    which index file to open."""
    run = RunFactory()
    for code in ("Ecom", "NDFsim", "PreviousCases"):
        RunCorpusFactory(run=run, corpus=CorpusFactory(code=code))

    assert run.corpora.count() == 3


def test_deleting_a_run_removes_its_restrictions() -> None:
    """The cascade at A restriction has no meaning without its run."""
    restriction = RunCorpusFactory()
    run = restriction.run

    run.delete()

    assert not type(restriction).objects.filter(pk=restriction.pk).exists()


def test_protect_refuses_to_delete_a_restricted_corpus() -> None:
    restriction = RunCorpusFactory()
    with pytest.raises(ProtectedError):
        restriction.corpus.delete()


# ----------------------------------------------------------------------------------------
# Queries: the constraint that replaces the sentinel.
# ----------------------------------------------------------------------------------------


def test_an_image_query_carries_a_probe_and_no_text() -> None:
    query = ImageQueryFactory()
    assert query.probe_content_id is not None
    assert query.query_text is None


def test_a_text_query_carries_text_and_no_probe() -> None:
    query = TextQueryFactory()
    assert query.probe_content_id is None
    assert query.query_text


@pytest.mark.parametrize(
    ("query_type", "with_probe", "text"),
    [
        (QueryType.IMAGE, False, None),
        (QueryType.IMAGE, True, "a black trainer"),
        (QueryType.TEXT, True, None),
        (QueryType.TEXT, False, None),
        (QueryType.TEXT, True, "a black trainer"),
    ],
    ids=[
        "image_without_probe",
        "image_with_text",
        "text_with_probe_and_no_text",
        "text_without_text",
        "text_with_probe",
    ],
)
def test_every_other_payload_combination_is_refused(
    query_type: str, with_probe: bool, text: str | None
) -> None:
    """This constraint is the replacement for the `TEXT_QUERY::` sentinel prefix, and the
 reason to prefer it is visible in the parametrisation: a sentinel makes five of these six
 combinations representable, and each one is a value some reader will interpret differently."""
    with pytest.raises(IntegrityError), transaction.atomic():
        ImageQueryFactory(
            query_type=query_type,
            probe_content=ContentObjectFactory if with_probe else None,
            query_text=text,
        )


def test_a_probe_image_is_stored_content_and_not_a_transient_upload() -> None:
    """A query image is evidence of what was asked, and an examination whose queries cannot
 be reproduced is not defensible - so the probe is digested and stored like any other content,
 and the content it points at cannot be removed while the query exists."""
    query = ImageQueryFactory()
    assert query.probe_content.sha256
    assert query.probe_content.storage_key
    with pytest.raises(ProtectedError):
        query.probe_content.delete()


def test_an_empty_text_query_is_refused() -> None:
    with pytest.raises(IntegrityError), transaction.atomic():
        TextQueryFactory(query_text="")


def test_a_text_query_beyond_the_length_limit_is_refused() -> None:
    """enforced at the database and not only at the serializer - which is what stops a
 management command or a data migration from writing a two-kilobyte-plus query."""
    with pytest.raises(IntegrityError), transaction.atomic():
        TextQueryFactory(query_text="a" * (MAX_QUERY_TEXT_LENGTH + 1))


def test_a_text_query_at_the_length_limit_is_accepted() -> None:
    assert TextQueryFactory(query_text="a" * MAX_QUERY_TEXT_LENGTH).pk is not None


def test_the_sequence_is_unique_within_a_run() -> None:
    first = ImageQueryFactory(sequence=1)
    with pytest.raises(IntegrityError), transaction.atomic():
        ImageQueryFactory(run=first.run, sequence=1)


def test_the_same_sequence_may_recur_in_another_run() -> None:
    ImageQueryFactory(sequence=1)
    assert ImageQueryFactory(sequence=1).pk is not None


def test_the_router_decision_is_recorded_and_starts_unset() -> None:
    """Null until the router has decided, which is distinct from either spectrum. Recorded rather
 than recomputed, because the routing rule may be revised and a past run's results must remain
 explicable under the rule that produced them."""
    assert ImageQueryFactory.routed_spectrum is None
    assert ImageQueryFactory(routed_spectrum="infrared").routed_spectrum == "infrared"


def test_an_unrecognised_query_status_is_refused() -> None:
    with pytest.raises(IntegrityError), transaction.atomic():
        ImageQueryFactory(status="queued")


@pytest.mark.parametrize("status", [s.value for s in QueryStatus])
def test_every_query_status_is_accepted(status: str) -> None:
    assert ImageQueryFactory(status=status).pk is not None


def test_the_result_count_starts_at_zero_and_is_materialised() -> None:
    """Materialised rather than aggregated, because a list response would otherwise issue
 one `COUNT` per row. Maintained by the retrieval service in the transaction that writes the
 results."""
    query = ImageQueryFactory()
    assert query.result_count == 0

    ResultFactory(query=query)
    Query.objects.filter(pk=query.pk).update(result_count=1)
    query.refresh_from_db()

    assert query.result_count == query.results.count() == 1


# ----------------------------------------------------------------------------------------
# Results: deduplication in the database.
# ----------------------------------------------------------------------------------------


def test_two_results_cannot_share_a_rank() -> None:
    first = ResultFactory(rank=1)
    with pytest.raises(IntegrityError), transaction.atomic():
        ResultFactory(query=first.query, rank=1)


def test_the_same_registration_cannot_appear_twice_in_one_query() -> None:
    """Deduplication in the database rather than in post-processing, because
 over-fetching across two encoders and then fusing is exactly the process that produces
 duplicates - and a duplicate occupies a result slot that a different image should have had."""
    first = ResultFactory(rank=1)
    with pytest.raises(IntegrityError), transaction.atomic():
        ResultFactory(query=first.query, rank=2, evidence_file=first.evidence_file)


def test_the_same_registration_may_appear_in_two_queries() -> None:
    first = ResultFactory()
    second = ResultFactory(evidence_file=first.evidence_file)
    assert first.query_id != second.query_id


@pytest.mark.parametrize("rank", [0, -1])
def test_a_rank_below_one_is_refused(rank: int) -> None:
    with pytest.raises(IntegrityError), transaction.atomic():
        ResultFactory(rank=rank)


@pytest.mark.parametrize(
    "scores",
    [
        {"score_fused": 1.5},
        {"score_fused": -1.5},
        {"score_model": 1.5},
        {"score_clip": 1.5},
        {"score_dinov2": -1.5, "score_clip": None},
        {"score_metadata": 1.5},
    ],
)
def test_a_score_outside_minus_one_to_one_is_refused(scores: dict[str, float | None]) -> None:
    """Cosine similarity is bounded, so a score outside the range is a fusion defect rather than
 an unusual result. The fused score is checked too: fusing bounded inputs under weights in zero
 to one is itself bounded, so a fused score out of range means the expression is wrong."""
    with pytest.raises(IntegrityError), transaction.atomic():
        ResultFactory(**scores)


@pytest.mark.parametrize("score", [-1.0, 0.0, 1.0])
def test_a_score_at_the_bound_is_accepted(score: float) -> None:
    assert ResultFactory(score_fused=score, score_model=score, score_clip=score).pk is not None


def test_a_result_with_neither_model_score_is_refused() -> None:
    with pytest.raises(IntegrityError), transaction.atomic():
        ResultFactory(score_clip=None, score_dinov2=None)


def test_an_unused_encoder_leaves_its_score_null() -> None:
    """Null and not zero. Zero is a legitimate cosine similarity - two orthogonal vectors - so
 writing it to mean "not used" would make an honest score indistinguishable from an absent one,
 and any average over the column silently wrong."""
    result = ResultFactory(score_clip=0.8, score_dinov2=None)
    assert result.score_dinov2 is None


def test_a_result_points_at_a_registration_and_not_at_a_path() -> None:
    """resolving the question recorded at A path is not an identity:
 it cannot be constrained, joined or access-controlled, and it stops being true the moment a
 mount is renamed."""
    result = ResultFactory()
    assert {field.name for field in Result._meta.get_fields}.isdisjoint(
        {"source_path", "path", "filename"}
    )
    assert result.evidence_file.corpus_id is not None


def test_protect_refuses_to_delete_a_returned_registration() -> None:
    result = ResultFactory()
    with pytest.raises(ProtectedError):
        result.evidence_file.delete()


def test_a_rerun_produces_a_new_run_rather_than_amending_one() -> None:
    """Results are immutable once written; `UPDATE` and `DELETE` are revoked on this table
 by a later migration. This asserts the shape that revocation protects: the second execution's
 results are reachable only through the second run, so a result set cited later is the one the
 system returned at the time."""
    case = CaseFactory()
    first_run = RunFactory(case=case, label="baseline")
    first_query = ImageQueryFactory(run=first_run, sequence=1)
    evidence = EvidenceFileFactory()
    ResultFactory(query=first_query, rank=1, evidence_file=evidence)

    second_run = RunFactory(case=case, label="baseline-rerun")
    second_query = ImageQueryFactory(run=second_run, sequence=1)
    ResultFactory(query=second_query, rank=1, evidence_file=evidence)

    assert Run.objects.filter(case=case).count() == 2
    assert Result.objects.filter(query__run=first_run).count() == 1
    assert Result.objects.filter(query__run=second_run).count() == 1
