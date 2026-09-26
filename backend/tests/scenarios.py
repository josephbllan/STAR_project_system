"""Composed states, built from the factories.

A factory produces one valid row. Most tests past this phase need a *situation*: a corpus that can
actually be searched, a case with a team on it, a run that has finished and left results, a result
that has been through review. Assembling those inline is where test suites start to rot - the same
eight lines appear in forty files, and when the model changes, thirty-nine of them are updated.

These are functions rather than fixtures on purpose. A fixture is resolved once per test and cannot
be asked for twice with different arguments, which is exactly what a test comparing two cases or two
corpora needs to do. `conftest.py` wraps the most common ones as fixtures for the tests that want
only one.

Every scenario here is built through the factories, so every row goes through the same constraints
as a row written by the application. None of them writes an audit event: the recorder arrives in
phase 3, and a scenario that faked one would make the later tests of the recorder pass against
fixture data rather than against the recorder ( 3).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from django.utils import timezone

from apps.accounts.models import Role
from apps.cases.models import AccessLevel, Case, Query, QueryStatus, Result, Run, RunStatus
from apps.datasets.models import Corpus, EvidenceFile
from apps.review.models import Approval, Note, Rating, Scope, Tag, TagAssignment
from apps.search.models import CLIP_DIMENSIONS, DINOV2_DIMENSIONS, Encoder
from tests.factories.accounts import InvestigatorFactory, ReviewerFactory, UserFactory
from tests.factories.cases import (
    CaseFactory,
    CaseMembershipFactory,
    ImageQueryFactory,
    ResultFactory,
    RunCorpusFactory,
    RunFactory,
)
from tests.factories.datasets import ContentObjectFactory, CorpusFactory, EvidenceFileFactory
from tests.factories.review import (
    DecidedApprovalFactory,
    NoteFactory,
    RatingFactory,
    TagAssignmentFactory,
    TagFactory,
)
from tests.factories.search import (
    ClipEmbeddingFactory,
    Dinov2EmbeddingFactory,
    Dinov2EncoderFactory,
    EncoderFactory,
    unit_vector,
)


@dataclass
class EncoderPair:
    """The two encoders a fused run uses.

 Held together because the dimensionalities differ (512 and 384) and a test that mixes them up
 gets a constraint violation rather than a wrong answer, which is the desired outcome but a
 confusing one to debug from a failure two functions away.
 """

    clip: Encoder
    dinov2: Encoder


@dataclass
class SearchableCorpus:
    """A corpus whose evidence can be retrieved: content, registration and vectors for both
 encoders.

 The distinction from a plain `CorpusFactory` is the vectors. A corpus without them is a
 perfectly valid corpus that returns nothing, and a retrieval test arranged that way passes its
 "no results" assertion for the wrong reason.
 """

    corpus: Corpus
    encoders: EncoderPair
    evidence: list[EvidenceFile] = field(default_factory=list)

    @property
    def size(self) -> int:
        return len(self.evidence)


@dataclass
class CaseTeam:
    """A case and the people on it, one membership per access level.

 `outsider` has no membership at all and is the most useful member of this structure: almost
 every authorisation test needs someone who should receive a 404 rather than a 403.
 """

    case: Case
    owner: object
    reader: object
    contributor: object
    outsider: object


@dataclass
class CompletedRun:
    """A run that has finished and left ranked results behind.

 Results are ranked from 1 with descending fused scores, because a test that sorts by score and a
 test that sorts by rank must not be able to disagree. `RunFactory` supplies a CLIP encoder only;
 this builder sets both, since a completed run is the state in which fusion has happened.
 """

    run: Run
    query: Query
    results: list[Result]
    corpus: SearchableCorpus

    @property
    def top(self) -> Result:
        return self.results[0]


def encoder_pair() -> EncoderPair:
    return EncoderPair(clip=EncoderFactory(), dinov2=Dinov2EncoderFactory())


def searchable_corpus(
    size: int = 5,
    *,
    encoders: EncoderPair | None = None,
    corpus: Corpus | None = None,
    case: Case | None = None,
) -> SearchableCorpus:
    """`size` evidence files, each with content and a vector from each encoder.

 `case` is the difference between shared-corpus evidence and case-scoped evidence, which are
 governed by different authorisation paths. It defaults to `None`, the shared form,
 because that is the one a test forgets to consider.

 Vectors are seeded from the index so that the ordering of a similarity search against any probe
 is deterministic. A random vector per row would make a recall assertion flap.
 """
    encoders = encoders or encoder_pair()
    corpus = corpus or CorpusFactory()

    evidence: list[EvidenceFile] = []
    for index in range(size):
        content = ContentObjectFactory()
        ClipEmbeddingFactory(
            content=content,
            encoder=encoders.clip,
            embedding=unit_vector(CLIP_DIMENSIONS, seed=index + 1),
        )
        Dinov2EmbeddingFactory(
            content=content,
            encoder=encoders.dinov2,
            embedding=unit_vector(DINOV2_DIMENSIONS, seed=index + 1),
        )
        evidence.append(EvidenceFileFactory(content=content, corpus=corpus, case=case))

    return SearchableCorpus(corpus=corpus, encoders=encoders, evidence=evidence)


def case_team(*, owner: object | None = None) -> CaseTeam:
    owner = owner or InvestigatorFactory
    case = CaseFactory(owner=owner)

    #: An analyst, which is `UserFactory`'s default role and the least-privileged one that can read
    #: a case at all.
    reader = UserFactory()
    contributor = InvestigatorFactory()
    CaseMembershipFactory(case=case, user=reader, access_level=AccessLevel.READ, granted_by=owner)
    CaseMembershipFactory(
        case=case, user=contributor, access_level=AccessLevel.CONTRIBUTE, granted_by=owner
    )

    return CaseTeam(
        case=case,
        owner=owner,
        reader=reader,
        contributor=contributor,
        outsider=UserFactory(role=Role.INVESTIGATOR),
    )


def completed_run(
    *,
    case: Case | None = None,
    corpus: SearchableCorpus | None = None,
    result_count: int = 5,
    created_by: object | None = None,
) -> CompletedRun:
    """A finished run with `result_count` ranked results drawn from the corpus.

 `result_count` is capped at the corpus size rather than silently producing fewer, because
 `uq_cases_result_query_file` refuses the same evidence file twice in one query, and a builder
 that wrapped around would fail with a constraint error saying nothing about the caller's
 mistake.
 """
    corpus = corpus or searchable_corpus(size=max(result_count, 1))
    if result_count > corpus.size:
        raise ValueError(
            f"asked for {result_count} results from a corpus of {corpus.size}; a query cannot "
            "return the same evidence file twice (uq_cases_result_query_file)"
        )

    case = case or CaseFactory
    created_by = created_by or case.owner
    finished = timezone.now()

    run = RunFactory(
        case=case,
        clip_encoder=corpus.encoders.clip,
        dinov2_encoder=corpus.encoders.dinov2,
        status=RunStatus.COMPLETE,
        started_at=finished - timezone.timedelta(seconds=12),
        finished_at=finished,
        created_by=created_by,
    )
    RunCorpusFactory(run=run, corpus=corpus.corpus)

    query = ImageQueryFactory(run=run, status=QueryStatus.COMPLETE, result_count=result_count)

    results = [
        ResultFactory(
            query=query,
            rank=rank,
            evidence_file=corpus.evidence[rank - 1],
            score_fused=1.0 - rank / 100,
            score_model=1.0 - rank / 100,
            score_clip=1.0 - rank / 100,
            score_dinov2=0.9 - rank / 100,
        )
        for rank in range(1, result_count + 1)
    ]

    return CompletedRun(run=run, query=query, results=results, corpus=corpus)


@dataclass
class ReviewedResult:
    """One result carrying every kind of review artefact, and an approval awaiting countersignature.

 The approval is left decided-but-not-countersigned because that is the state the two-person rule
 exists to describe. A countersigned approval is a finished thing; this one still has a
 decision to test.
 """

    result: Result
    rating: Rating
    note: Note
    tag: Tag
    assignment: TagAssignment
    approval: Approval
    reviewer: object


def reviewed_result(
    *, result: Result | None = None, reviewer: object | None = None
) -> ReviewedResult:
    result = result or completed_run(result_count=1).top
    reviewer = reviewer or ReviewerFactory
    tag = TagFactory()
    return ReviewedResult(
        result=result,
        rating=RatingFactory(scope=Scope.RESULT, result=result, author=reviewer, value=4),
        note=NoteFactory(scope=Scope.RESULT, result=result, author=reviewer),
        tag=tag,
        assignment=TagAssignmentFactory(
            scope=Scope.RESULT, result=result, tag=tag, assigned_by=reviewer
        ),
        approval=DecidedApprovalFactory(result=result, decided_by=reviewer),
        reviewer=reviewer,
    )
