"""Cases, membership, runs, queries and results.

`RunFactory` supplies a CLIP encoder and no DINOv2 encoder. That is a real configuration rather
than a convenience: `ck_cases_run_encoder_present` requires at least one, and a factory defaulting
to both would mean no test ever exercised the single-encoder path that the constraint exists to
permit.
"""

from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

import factory
from django.utils import timezone

from apps.cases.models import (
    AccessLevel,
    Case,
    CaseMembership,
    CaseStatus,
    Preprocessing,
    Query,
    QueryStatus,
    QueryType,
    Result,
    Run,
    RunCorpus,
    RunStatus,
)
from tests.factories.accounts import InvestigatorFactory, UserFactory
from tests.factories.datasets import ContentObjectFactory, CorpusFactory, EvidenceFileFactory
from tests.factories.search import EncoderFactory


class CaseFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Case

    public_id = factory.LazyFunction(uuid4)
    name = factory.Sequence(lambda n: f"Operation {n}")
    reference = ""
    description = ""
    owner = factory.SubFactory(InvestigatorFactory)
    status = CaseStatus.OPEN
    # Derived, so that switching `status` to closed does not trip
    # `ck_cases_case_closed_at_paired` on the way past.
    closed_at = factory.LazyAttribute(
        lambda o: timezone.now() if o.status == CaseStatus.CLOSED else None
    )


class ClosedCaseFactory(CaseFactory):
    status = CaseStatus.CLOSED


class CaseMembershipFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = CaseMembership

    case = factory.SubFactory(CaseFactory)
    user = factory.SubFactory(UserFactory)
    access_level = AccessLevel.READ
    granted_by = factory.SubFactory(InvestigatorFactory)
    revoked_at = None
    revoked_by = None


class RevokedCaseMembershipFactory(CaseMembershipFactory):
    """Both revocation columns together, because `ck_cases_casemembership_revocation_pair` refuses
 one without the other: a revocation nobody is accountable for is not a revocation."""

    revoked_at = factory.LazyFunction(timezone.now)
    revoked_by = factory.SubFactory(InvestigatorFactory)


class RunFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Run

    public_id = factory.LazyFunction(uuid4)
    case = factory.SubFactory(CaseFactory)
    label = factory.Sequence(lambda n: f"run-{n}")
    model_weight = Decimal("0.500")
    metadata_weight = Decimal("0.300")
    top_k = 50
    clip_encoder = factory.SubFactory(EncoderFactory)
    dinov2_encoder = None
    status = RunStatus.PENDING
    created_by = factory.SubFactory(InvestigatorFactory)


class RunCorpusFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = RunCorpus

    run = factory.SubFactory(RunFactory)
    corpus = factory.SubFactory(CorpusFactory)


class ImageQueryFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Query

    public_id = factory.LazyFunction(uuid4)
    run = factory.SubFactory(RunFactory)
    sequence = factory.Sequence(lambda n: n + 1)
    query_type = QueryType.IMAGE
    #: The probe is stored content, not a transient upload.
    probe_content = factory.SubFactory(ContentObjectFactory)
    query_text = None
    preprocessing = Preprocessing.NONE
    status = QueryStatus.PENDING


class TextQueryFactory(ImageQueryFactory):
    query_type = QueryType.TEXT
    probe_content = None
    query_text = "a black trainer with a herringbone tread"


class ResultFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Result

    public_id = factory.LazyFunction(uuid4)
    query = factory.SubFactory(ImageQueryFactory)
    rank = factory.Sequence(lambda n: n + 1)
    evidence_file = factory.SubFactory(EvidenceFileFactory)
    score_fused = 0.82
    score_model = 0.79
    score_clip = 0.79
    score_dinov2 = None
    score_metadata = None
