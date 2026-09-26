"""Tests for the scenario builders themselves.

Test infrastructure that is not itself tested is the worst kind: when a suite of forty tests starts
failing, the first question is whether the system broke or the arrangement did, and without these
that question takes an afternoon.

What is asserted here is the *claim each builder makes in its docstring* - that a searchable corpus
is actually searchable, that a completed run's ranks agree with its scores, that the outsider really
is outside. A builder whose claim quietly stops holding would otherwise make every test that relies
on it pass for a reason nobody intended.
"""

from __future__ import annotations

import pytest

from apps.cases.models import AccessLevel, CaseMembership, QueryStatus, RunStatus
from apps.review.models import Scope
from apps.search.models import (
    CLIP_DIMENSIONS,
    DINOV2_DIMENSIONS,
    ClipEmbedding,
    Dinov2Embedding,
)
from tests import scenarios

pytestmark = [pytest.mark.integration, pytest.mark.django_db]


def test_an_encoder_pair_is_two_families_at_two_widths() -> None:
    pair = scenarios.encoder_pair()
    assert pair.clip.dimensions == CLIP_DIMENSIONS
    assert pair.dinov2.dimensions == DINOV2_DIMENSIONS
    assert pair.clip.family != pair.dinov2.family


def test_a_searchable_corpus_has_a_vector_per_encoder_per_file() -> None:
    """The claim that distinguishes this builder from `CorpusFactory`. A corpus without vectors
 returns nothing, and a retrieval test arranged that way passes its "no results" assertion
 without the retrieval code having been exercised at all."""
    built = scenarios.searchable_corpus(size=4)

    contents = [evidence.content_id for evidence in built.evidence]
    assert len(contents) == 4
    assert (
        ClipEmbedding.objects.filter(content_id__in=contents, encoder=built.encoders.clip).count()
        == 4
    )
    assert (
        Dinov2Embedding.objects.filter(
            content_id__in=contents, encoder=built.encoders.dinov2
        ).count()
        == 4
    )


def test_the_vectors_of_a_searchable_corpus_are_distinct() -> None:
    """Seeded from the row index rather than a constant. Identical vectors would make every
 similarity ranking a tie, and a ranking test would then be asserting the tiebreak."""
    built = scenarios.searchable_corpus(size=3)
    vectors = {
        tuple(row.embedding) for row in ClipEmbedding.objects.filter(encoder=built.encoders.clip)
    }
    assert len(vectors) == 3


def test_a_searchable_corpus_is_shared_evidence_unless_a_case_is_named() -> None:
    """The two forms are governed by different authorisation paths, and the shared form is
 the one a test forgets to consider, so it is the default."""
    assert all(
        evidence.case_id is None for evidence in scenarios.searchable_corpus(size=2).evidence
    )


def test_case_scoped_evidence_is_available_by_asking_for_it() -> None:
    team = scenarios.case_team
    built = scenarios.searchable_corpus(size=2, case=team.case)
    assert all(evidence.case_id == team.case.pk for evidence in built.evidence)


def test_two_corpora_can_share_one_encoder_pair() -> None:
    """The reason these are functions and not fixtures. A test comparing retrieval across two
 corpora must hold the encoders constant, or it is comparing two variables at once."""
    pair = scenarios.encoder_pair()
    first = scenarios.searchable_corpus(size=2, encoders=pair)
    second = scenarios.searchable_corpus(size=2, encoders=pair)

    assert first.corpus != second.corpus
    assert first.encoders.clip == second.encoders.clip


def test_a_case_team_has_one_membership_per_access_level() -> None:
    team = scenarios.case_team
    levels = set(
        CaseMembership.objects.filter(case=team.case, revoked_at__isnull=True).values_list(
            "access_level", flat=True
        )
    )
    assert levels == {AccessLevel.READ, AccessLevel.CONTRIBUTE}


def test_the_outsider_of_a_case_team_has_no_membership() -> None:
    """The most useful member of the structure. Almost every authorisation test needs someone who
 should receive a 404 rather than a 403, because a 403 confirms the case exists."""
    team = scenarios.case_team
    assert not CaseMembership.objects.filter(case=team.case, user=team.outsider).exists()


def test_the_owner_of_a_case_team_is_not_a_member_row() -> None:
    """Ownership is a column on the case, not a membership. Recorded because a permission check that
 looked only at `CaseMembership` would lock the owner out of their own case, and this is where
 that assumption is visible."""
    team = scenarios.case_team
    assert team.case.owner == team.owner
    assert not CaseMembership.objects.filter(case=team.case, user=team.owner).exists()


def test_a_completed_run_is_finished_and_timed() -> None:
    built = scenarios.completed_run
    assert built.run.status == RunStatus.COMPLETE
    assert built.query.status == QueryStatus.COMPLETE
    assert built.run.started_at is not None
    assert built.run.finished_at > built.run.started_at


def test_a_completed_run_uses_both_encoders() -> None:
    """`RunFactory` supplies CLIP only, which is a real single-encoder configuration. A *completed*
 run is the state in which fusion has happened, so this builder sets both."""
    built = scenarios.completed_run
    assert built.run.clip_encoder_id is not None
    assert built.run.dinov2_encoder_id is not None


def test_the_ranks_of_a_completed_run_agree_with_its_scores() -> None:
    """A test that sorts by rank and a test that sorts by score must not be able to disagree, or one
 of them is asserting the wrong ordering and both appear to pass."""
    built = scenarios.completed_run(result_count=4)

    assert [result.rank for result in built.results] == [1, 2, 3, 4]
    scores = [result.score_fused for result in built.results]
    assert scores == sorted(scores, reverse=True)
    assert built.top.rank == 1


def test_a_completed_run_searches_the_corpus_it_returned_results_from() -> None:
    built = scenarios.completed_run(result_count=3)
    assert set(built.run.corpora.values_list("corpus_id", flat=True)) == {built.corpus.corpus.pk}
    assert {result.evidence_file.corpus_id for result in built.results} == {built.corpus.corpus.pk}


def test_the_result_count_of_a_query_matches_the_results_written() -> None:
    built = scenarios.completed_run(result_count=3)
    assert built.query.result_count == built.query.results.count() == 3


def test_asking_for_more_results_than_the_corpus_holds_is_refused_clearly() -> None:
    """`uq_cases_result_query_file` refuses the same evidence file twice in one query, and a builder
 that wrapped around would fail with a constraint error saying nothing about the caller's
 mistake."""
    corpus = scenarios.searchable_corpus(size=2)
    with pytest.raises(ValueError, match="cannot return the same evidence file twice"):
        scenarios.completed_run(corpus=corpus, result_count=3)


def test_a_reviewed_result_carries_every_kind_of_review_artefact() -> None:
    built = scenarios.reviewed_result

    assert built.rating.scope == Scope.RESULT
    assert built.rating.result_id == built.result.pk
    assert built.note.result_id == built.result.pk
    assert built.assignment.tag_id == built.tag.pk
    assert built.approval.result_id == built.result.pk


def test_a_reviewed_result_is_decided_but_not_countersigned() -> None:
    """Left in the state the two-person rule exists to describe: a countersigned approval is
 a finished thing, whereas this one still has a decision to test."""
    built = scenarios.reviewed_result
    assert built.approval.decided_by_id == built.reviewer.pk
    assert built.approval.countersigned_by_id is None


def test_no_scenario_writes_an_audit_event() -> None:
    """The recorder arrives in phase 3. A scenario that faked an event would make the later tests of
 the recorder pass against fixture data rather than against the recorder."""
    from apps.audit.models import AuditEvent

    scenarios.reviewed_result
    scenarios.case_team
    assert not AuditEvent.objects.exists()


def test_the_fixtures_wrap_the_builders(
    case_team,
    searchable_corpus,
    completed_run,  # noqa: ANN001
) -> None:
    """One test for all three fixtures, because what is being checked is that they are wired at
 all - a fixture that raised on resolution would otherwise show up as a failure in whichever
 unrelated test used it first."""
    assert case_team.case.pk is not None
    assert searchable_corpus.size == 5
    assert len(completed_run.results) == 5
