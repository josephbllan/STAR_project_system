"""Cases, membership, retrieval runs and their results.

The case is both the unit of investigation and the unit of access control. Effective authority
is the intersection of role and membership, so membership lives with the thing it scopes.

Case names are unique per owner, not globally. A run records the fusion weights it actually
used rather than reading them later from configuration. A corpus restriction is a predicate
joined in the same query as the metadata (`RunCorpus`), not a choice of index file. A result
points at a registration, not at a path: a path cannot be constrained, joined or
access-controlled, and it stops being true the moment a mount is renamed.
"""

from __future__ import annotations

from django.conf import settings
from django.db import models
from django.db.models import Q
from django.db.models.functions import Now
from django.utils import timezone

from apps.common.models import PublicIdModel, TimeStampedModel
from apps.common.querysets import ScopedManager

#: The ceiling on a requested result count, and the same number a serializer enforces
#: first so that the refusal carries a field name rather than a constraint violation.
MAX_TOP_K = 500
MAX_QUERY_TEXT_LENGTH = 2000

#: Over-fetch, then discard registrations outside the run's corpora and those whose file is
#: missing, then truncate to `top_k`. Filtering inside the approximate search is not something
#: HNSW supports without losing the recall guarantee the index exists to provide.
DEFAULT_OVERFETCH_FACTOR = 6
DEFAULT_OVERFETCH_CEILING = 500


class AccessLevel(models.TextChoices):
    READ = "read", "Read"
    CONTRIBUTE = "contribute", "Contribute"
    REVIEW = "review", "Review"


class CaseStatus(models.TextChoices):
    OPEN = "open", "Open"
    UNDER_REVIEW = "under_review", "Under review"
    CLOSED = "closed", "Closed"


class RunStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    RUNNING = "running", "Running"
    COMPLETE = "complete", "Complete"
    FAILED = "failed", "Failed"
    CANCELLED = "cancelled", "Cancelled"


#: The operational view of work in flight, and the condition on `ix_cases_run_status_pending`.
RUN_IN_FLIGHT_STATUSES = (RunStatus.PENDING, RunStatus.RUNNING)


class QueryType(models.TextChoices):
    IMAGE = "image", "Image"
    TEXT = "text", "Text"


class Preprocessing(models.TextChoices):
    NONE = "none", "None"
    HISTOGRAM_EQUALISATION = "histogram_equalisation", "Histogram equalisation"


class Spectrum(models.TextChoices):
    """The query router's decision. Recorded rather than recomputed, because the routing rule may
 be revised and a past run's results have to remain explicable under the rule that produced
 them."""

    VISIBLE = "visible", "Visible"
    INFRARED = "infrared", "Infrared"


class QueryStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    ENCODING = "encoding", "Encoding"
    SEARCHING = "searching", "Searching"
    COMPLETE = "complete", "Complete"
    FAILED = "failed", "Failed"


class Case(TimeStampedModel, PublicIdModel):
    """The unit of investigation and the unit of access control.

 No `DELETE` privilege is granted on this table by a later migration, which realises at the
 database rather than in a serializer - the difference being that a serializer can be bypassed by
 a management command and a privilege cannot.
 """

    name = models.CharField(max_length=200)
    reference = models.CharField(max_length=100, default="", blank=True)
    #: Sanitised at storage rather than at render. Sanitising on the way out means every
    #: consumer has to remember to do it; sanitising on the way in means the stored value is safe
    #: for a consumer that forgets.
    description = models.TextField(default="", blank=True)

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="owned_cases",
        db_index=False,
    )
    status = models.CharField(max_length=16, choices=CaseStatus.choices, default=CaseStatus.OPEN)
    opened_at = models.DateTimeField(default=timezone.now, db_default=Now())
    closed_at = models.DateTimeField(null=True, blank=True)
    status_changed_at = models.DateTimeField(default=timezone.now, db_default=Now())

    # The case *is* the scope, so the path to it is empty. Every other scoped model
    # in the project reaches a case through this one.
    objects = ScopedManager(case_paths=("",))

    class Meta:
        constraints = [
            # Per owner, not globally. Two investigators may each have a case called
            # "Operation Harrier" without either having to discover the other's naming.
            models.UniqueConstraint(fields=["owner", "name"], name="uq_cases_case_owner_name"),
            models.UniqueConstraint(fields=["public_id"], name="uq_cases_case_public_id"),
            models.CheckConstraint(
                condition=Q(status__in=[s.value for s in CaseStatus]),
                name="ck_cases_case_status_valid",
            ),
            # Both directions: a closed case without a closing time, or a closing time on
            # a case that is open, is a record that cannot be reported from.
            models.CheckConstraint(
                condition=Q(status=CaseStatus.CLOSED, closed_at__isnull=False)
                | (~Q(status=CaseStatus.CLOSED) & Q(closed_at__isnull=True)),
                name="ck_cases_case_closed_at_paired",
            ),
        ]
        indexes = [
            models.Index(fields=["owner", "status"], name="ix_cases_case_owner_status"),
            models.Index(fields=["status", "-opened_at"], name="ix_cases_case_status_opened"),
        ]

    def __str__(self) -> str:
        return self.name


class CaseMembership(TimeStampedModel):
    """A named user's access level on a specific case.

 Revocation sets `revoked_at`; it never deletes. The record that someone had access, and who
 granted it, is the substance of the access trail - a deleted row answers no question later.

 This is why `uq_cases_casemembership_active` is a *partial* unique index rather than a
 `unique_together`: uniqueness applies among active memberships only, so a user may be granted
 access, have it revoked, and be granted it again, leaving three rows and one live grant.
 Django's `unique_together` cannot express the condition.
 """

    case = models.ForeignKey(
        Case, on_delete=models.PROTECT, related_name="memberships", db_index=False
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="case_memberships",
        db_index=False,
    )
    access_level = models.CharField(max_length=16, choices=AccessLevel.choices)

    granted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="granted_memberships"
    )
    granted_at = models.DateTimeField(default=timezone.now, db_default=Now())
    revoked_at = models.DateTimeField(null=True, blank=True)
    revoked_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="revoked_memberships",
    )

    # Scoped like anything else attached to a case: a caller sees the membership rows of cases they
    # own or belong to, which is how a case's member list is served without a second rule.
    objects = ScopedManager(case_paths=("case",))

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["case", "user"],
                condition=Q(revoked_at__isnull=True),
                name="uq_cases_casemembership_active",
            ),
            models.CheckConstraint(
                condition=Q(access_level__in=[a.value for a in AccessLevel]),
                name="ck_cases_casemembership_access_valid",
            ),
            # A revocation with no revoking actor is a revocation nobody is accountable for.
            models.CheckConstraint(
                condition=Q(revoked_at__isnull=True, revoked_by__isnull=True)
                | Q(revoked_at__isnull=False, revoked_by__isnull=False),
                name="ck_cases_casemembership_revocation_pair",
            ),
        ]
        indexes = [
            # The most frequently executed authorisation query in the system: it runs on every
            # case-scoped collection, for every request. Partial, because a revoked
            # membership never contributes to authority and should not be paged in to be skipped.
            models.Index(
                fields=["user", "case"],
                condition=Q(revoked_at__isnull=True),
                name="ix_cases_casemembership_user_active",
            ),
            # Every
            # other index on this table is partial on `revoked_at IS NULL`, which serves
            # authorisation and nothing else. The access-history panel asks the opposite question -
            # who has *ever* held access to this case, and who granted and revoked it - and that
            # query has no index at all without this one.
            models.Index(fields=["case"], name="ix_cases_casemembership_case"),
        ]

    def __str__(self) -> str:
        return f"{self.user_id} {self.access_level} on {self.case_id}"

    @property
    def is_active(self) -> bool:
        return self.revoked_at is None


class Run(TimeStampedModel, PublicIdModel):
    """One execution of retrieval within a case.

 The weights are columns and not entries in `params` because they are what a reviewer filters
 and compares on. `params` holds what is genuinely variable between runs, which is the
 case JSON is for rather than an excuse to avoid deciding what a column should be.
 """

    case = models.ForeignKey(Case, on_delete=models.PROTECT, related_name="runs", db_index=False)
    label = models.CharField(max_length=200)

    #: Lambda: the DINOv2 share of the model score. `numeric` and not `double precision` because
    #: these are configured values that must round-trip exactly - unlike the scores below, which
    #: originate as float32 inner products and would be lying if stored as exact decimals.
    model_weight = models.DecimalField(max_digits=4, decimal_places=3)
    #: One minus alpha: the metadata share of the final score.
    metadata_weight = models.DecimalField(max_digits=4, decimal_places=3)

    top_k = models.IntegerField()
    overfetch_factor = models.IntegerField(default=DEFAULT_OVERFETCH_FACTOR)
    overfetch_ceiling = models.IntegerField(default=DEFAULT_OVERFETCH_CEILING)

    #: Nullable individually, but not both: a run with no encoder cannot produce a score.
    clip_encoder = models.ForeignKey(
        "search.Encoder",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="clip_runs",
    )
    dinov2_encoder = models.ForeignKey(
        "search.Encoder",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="dinov2_runs",
    )

    params = models.JSONField(default=dict)
    status = models.CharField(max_length=16, choices=RunStatus.choices, default=RunStatus.PENDING)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="created_runs"
    )
    task_run = models.ForeignKey(
        "tasks.TaskRun",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="retrieval_runs",
    )
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    objects = ScopedManager(case_paths=("case",))

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["case", "label"], name="uq_cases_run_case_label"),
            models.UniqueConstraint(fields=["public_id"], name="uq_cases_run_public_id"),
            # The fusion expression is meaningless outside this range: a weight above one
            # amplifies one component beyond the score it is a share of.
            models.CheckConstraint(
                condition=Q(model_weight__gte=0)
                & Q(model_weight__lte=1)
                & Q(metadata_weight__gte=0)
                & Q(metadata_weight__lte=1),
                name="ck_cases_run_weights_range",
            ),
            models.CheckConstraint(
                condition=Q(top_k__gte=1) & Q(top_k__lte=MAX_TOP_K),
                name="ck_cases_run_top_k_range",
            ),
            models.CheckConstraint(
                condition=Q(overfetch_factor__gte=1)
                & Q(overfetch_factor__lte=20)
                & Q(overfetch_ceiling__gte=1)
                & Q(overfetch_ceiling__lte=5000),
                name="ck_cases_run_overfetch_sane",
            ),
            models.CheckConstraint(
                condition=Q(status__in=[s.value for s in RunStatus]),
                name="ck_cases_run_status_valid",
            ),
            models.CheckConstraint(
                condition=Q(clip_encoder__isnull=False) | Q(dinov2_encoder__isnull=False),
                name="ck_cases_run_encoder_present",
            ),
        ]
        indexes = [
            models.Index(fields=["case", "-created_at"], name="ix_cases_run_case_created"),
            models.Index(
                fields=["status"],
                condition=Q(status__in=[s.value for s in RUN_IN_FLIGHT_STATUSES]),
                name="ix_cases_run_status_pending",
            ),
        ]

    def __str__(self) -> str:
        return self.label


class RunCorpus(TimeStampedModel):
    """Restricts a run to a set of corpora.

    A predicate joined in the same query as the metadata, rather than a choice of which
    index file to open.
    """

    #: CASCADE, and one of the four in the schema. A restriction has no meaning without
    #: the run it restricts.
    run = models.ForeignKey(Run, on_delete=models.CASCADE, related_name="corpora", db_index=False)
    corpus = models.ForeignKey(
        "datasets.Corpus", on_delete=models.PROTECT, related_name="run_restrictions"
    )

    objects = ScopedManager(case_paths=("run__case",))

    class Meta:
        verbose_name_plural = "run corpora"
        constraints = [
            models.UniqueConstraint(fields=["run", "corpus"], name="uq_cases_runcorpus_run_corpus"),
        ]

    def __str__(self) -> str:
        return f"{self.run_id} restricted to {self.corpus_id}"


class Query(TimeStampedModel, PublicIdModel):
    """One query within a run.

    `ck_cases_query_type_payload` keeps the query type in a column. A sentinel prefix
    encoded the type inside the value it was qualifying, so every reader had to know the
    convention and a value that happened to begin with the prefix was indistinguishable
    from a text query. The two payload columns are mutually exclusive.
    """

    run = models.ForeignKey(Run, on_delete=models.PROTECT, related_name="queries", db_index=False)
    sequence = models.IntegerField()
    query_type = models.CharField(max_length=8, choices=QueryType.choices)

    #: The stored probe image. A probe is itself content: digested and stored, not
    #: transient, because a query image is evidence of what was asked and an examination whose
    #: queries cannot be reproduced is not defensible.
    probe_content = models.ForeignKey(
        "datasets.ContentObject",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="probe_queries",
    )
    # DJ001: null means "this is not a text query", which the payload check enforces. An empty
    # string would satisfy `IS NOT NULL` and make a text query with no text representable.
    query_text = models.TextField(null=True, blank=True)  # noqa: DJ001

    preprocessing = models.CharField(
        max_length=24, choices=Preprocessing.choices, default=Preprocessing.NONE
    )
    # DJ001: null until the router has decided, which is distinct from either spectrum.
    routed_spectrum = models.CharField(  # noqa: DJ001
        max_length=16, choices=Spectrum.choices, null=True, blank=True
    )
    status = models.CharField(
        max_length=16, choices=QueryStatus.choices, default=QueryStatus.PENDING
    )
    task_run = models.ForeignKey(
        "tasks.TaskRun",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="queries",
    )
    #: Materialised rather than aggregated, because a list response would otherwise issue one
    #: `COUNT` per row. Maintained by the retrieval service in the same transaction that
    #: writes the results.
    result_count = models.IntegerField(default=0)

    objects = ScopedManager(case_paths=("run__case",))

    class Meta:
        verbose_name_plural = "queries"
        constraints = [
            models.UniqueConstraint(fields=["run", "sequence"], name="uq_cases_query_run_sequence"),
            models.UniqueConstraint(fields=["public_id"], name="uq_cases_query_public_id"),
            models.CheckConstraint(
                condition=Q(
                    query_type=QueryType.IMAGE,
                    probe_content__isnull=False,
                    query_text__isnull=True,
                )
                | Q(
                    query_type=QueryType.TEXT,
                    query_text__isnull=False,
                    probe_content__isnull=True,
                ),
                name="ck_cases_query_type_payload",
            ),
            models.CheckConstraint(
                condition=Q(query_text__isnull=True)
                | (Q(query_text__length__gte=1) & Q(query_text__length__lte=MAX_QUERY_TEXT_LENGTH)),
                name="ck_cases_query_text_length",
            ),
            models.CheckConstraint(
                condition=Q(status__in=[s.value for s in QueryStatus]),
                name="ck_cases_query_status_valid",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.query_type} {self.sequence} of run {self.run_id}"


class Result(TimeStampedModel, PublicIdModel):
    """One ranked result.

 Immutable once written: re-running retrieval produces a new run rather than amending
 an existing one, and no `UPDATE` or `DELETE` privilege is granted on this table. That is the
 only way a result set can be cited later as what the system returned at the time.

 The two unique constraints do different jobs. `(query, rank)` makes presentation deterministic;
 `(query, evidence_file)` enforces deduplication **in the database** rather than by
 post-processing, which matters because over-fetching across two encoders and then
 fusing is precisely the process that produces duplicates.
 """

    query = models.ForeignKey(
        Query, on_delete=models.PROTECT, related_name="results", db_index=False
    )
    rank = models.IntegerField()
    #: A path cannot be constrained, joined or access-controlled, and it stops being true
    #: when a mount is renamed. The result points at the registration instead.
    evidence_file = models.ForeignKey(
        "datasets.EvidenceFile",
        on_delete=models.PROTECT,
        related_name="results",
        db_index=False,
    )

    #: `double precision` and not `numeric`: these originate as 32-bit inner products, and storing
    #: them as exact decimals would assert a precision the computation does not have.
    score_fused = models.FloatField()
    score_model = models.FloatField()
    score_clip = models.FloatField(null=True, blank=True)
    score_dinov2 = models.FloatField(null=True, blank=True)
    score_metadata = models.FloatField(null=True, blank=True)

    # Three joins from the case. This is the deepest scoped path in the schema, and it is the one
    # that matters most: a result names an evidence file, so an unscoped result collection would
    # disclose which evidence appears in another investigator's case.
    objects = ScopedManager(case_paths=("query__run__case",))

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["query", "rank"], name="uq_cases_result_query_rank"),
            models.UniqueConstraint(
                fields=["query", "evidence_file"], name="uq_cases_result_query_file"
            ),
            models.UniqueConstraint(fields=["public_id"], name="uq_cases_result_public_id"),
            models.CheckConstraint(condition=Q(rank__gte=1), name="ck_cases_result_rank_positive"),
            # Cosine similarity is bounded, so a score outside the range is a fusion defect and
            # not an unusual result. Every score column, including the fused one, because the
            # fusion of bounded inputs under weights in [0, 1] is itself bounded.
            models.CheckConstraint(
                condition=Q(score_fused__gte=-1)
                & Q(score_fused__lte=1)
                & Q(score_model__gte=-1)
                & Q(score_model__lte=1)
                & (Q(score_clip__isnull=True) | (Q(score_clip__gte=-1) & Q(score_clip__lte=1)))
                & (
                    Q(score_dinov2__isnull=True)
                    | (Q(score_dinov2__gte=-1) & Q(score_dinov2__lte=1))
                )
                & (
                    Q(score_metadata__isnull=True)
                    | (Q(score_metadata__gte=-1) & Q(score_metadata__lte=1))
                ),
                name="ck_cases_result_scores_range",
            ),
            models.CheckConstraint(
                condition=Q(score_clip__isnull=False) | Q(score_dinov2__isnull=False),
                name="ck_cases_result_at_least_one_model_score",
            ),
        ]
        indexes = [
            # "In which runs has this image been returned?" - used by review now and by cross-case
            # correlation later.
            models.Index(fields=["evidence_file"], name="ix_cases_result_evidence_file"),
        ]

    def __str__(self) -> str:
        return f"#{self.rank} of query {self.query_id}"
