"""Encoders and vectors.

Vectors live in PostgreSQL so writes are transactional, corpus restriction is a predicate in the
same query as the metadata, and there is one datastore to back up.

An embedding references content, not registration: a vector is a property of the bytes.
Re-registering the same bytes in another corpus does not produce another vector.

Normalisation is not performed here. Vectors are L2-normalised on write in `retrieval/` so that
cosine distance and inner product induce the same ordering. The model layer stores what it is
given.

HNSW indexes are built by `search.0002` in a non-atomic migration using
`CREATE INDEX CONCURRENTLY` once a corpus is populated. An index built over an empty table has
to be rebuilt to be of any use.
"""

from __future__ import annotations

from django.db import models
from django.db.models import Q
from django.db.models.functions import Now
from django.utils import timezone
from pgvector.django import VectorField

from apps.common.models import TimeStampedModel

#: Fixed by the encoders themselves, not configuration. `openai/clip-vit-base-patch32` emits 512
#: and `vit_small_patch14_dinov2.lvd142m` emits 384, and a column cannot hold the other one.
CLIP_DIMENSIONS = 512
DINOV2_DIMENSIONS = 384


class EncoderFamily(models.TextChoices):
    """Determines which embedding table receives an encoder's output, which is why the family is a
 column rather than something inferred from the name at read time."""

    CLIP = "clip", "CLIP"
    DINOV2 = "dinov2", "DINOv2"


#: The one place the correspondence is written down. Both the check constraint and the routing of
#: an encoder to its table read it, so the two cannot drift apart.
FAMILY_DIMENSIONS: dict[str, int] = {
    EncoderFamily.CLIP: CLIP_DIMENSIONS,
    EncoderFamily.DINOV2: DINOV2_DIMENSIONS,
}


class Encoder(TimeStampedModel):
    """An encoder as the triple of name, version and preprocessing version.

 Two rows differing only in `preprocess_version` are two distinct encoders. That is the whole
 reason the column is part of the identity: a change to resizing, cropping or colour handling
 changes the vectors an otherwise identical model produces, and comparing a vector made under
 one preprocessing pipeline against a vector made under another is a comparison of two
 different things that will nevertheless return a number.
 """

    name = models.CharField(max_length=100)
    family = models.CharField(max_length=16, choices=EncoderFamily.choices)
    version = models.CharField(max_length=50)
    preprocess_version = models.CharField(max_length=50)
    dimensions = models.IntegerField()
    is_active = models.BooleanField(default=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["name", "version", "preprocess_version"],
                name="uq_search_encoder_identity",
            ),
            models.CheckConstraint(
                condition=Q(family__in=[f.value for f in EncoderFamily]),
                name="ck_search_encoder_family_valid",
            ),
            # Prevents registering an encoder whose output cannot fit the table its family routes
            # it to. Without this the mismatch would surface as a rejected insert during
            # the first encoding batch, long after the registration that caused it.
            models.CheckConstraint(
                condition=Q(family=EncoderFamily.CLIP, dimensions=CLIP_DIMENSIONS)
                | Q(family=EncoderFamily.DINOV2, dimensions=DINOV2_DIMENSIONS),
                name="ck_search_encoder_dimensions_match_family",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.name}@{self.version}/{self.preprocess_version}"


class Embedding(TimeStampedModel):
    """Everything the two embedding tables share, which is everything but the vector itself.

 One table per dimensionality rather than one table with a nullable column per
 width or an unconstrained `vector` column: pgvector needs a declared dimensionality to build an
 index, and a single table would have meant either an index per subset of rows or no index.

 The names of the constraints and indexes below interpolate `%(class)s`, so the two concrete
 tables get the names 5.2 gives them without either being written twice.
 """

    #: CASCADE, and one of only four in the schema. An embedding cannot meaningfully exist
    #: without the bytes it describes, and it is derived data that can be recomputed. Content is
    #: never deleted through the application in any case, so this is what should happen if
    #: a migration or an administrator ever does remove a row, rather than a RESTRICT that would
    #: leave the removal to be completed by hand.
    #:
    #: `db_index=False` because `uq_search_%(class)s_content_encoder` already leads with this
    #: column.
    content = models.ForeignKey(
        "datasets.ContentObject",
        on_delete=models.CASCADE,
        related_name="%(class)ss",
        db_index=False,
    )
    #: RESTRICT: an encoder row is the only record of how these vectors were produced, so it
    #: outlives them. `db_index=False` because `ix_search_%(class)s_encoder` covers it.
    encoder = models.ForeignKey(
        Encoder, on_delete=models.PROTECT, related_name="%(class)ss", db_index=False
    )
    encoded_at = models.DateTimeField(default=timezone.now, db_default=Now())
    task_run = models.ForeignKey(
        "tasks.TaskRun",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="%(class)ss",
    )

    class Meta:
        abstract = True
        constraints = [
            # One vector per encoder per byte sequence. This is what makes re-encoding an
            # update rather than an accumulation, and what stops a redelivered encoding task from
            # putting a second copy of the same vector into the index.
            models.UniqueConstraint(
                fields=["content", "encoder"],
                name="uq_search_%(class)s_content_encoder",
            ),
        ]
        indexes = [
            # Coverage reporting - which content lacks a vector for which encoder - and the
            # re-encoding sweep that follows a preprocessing change.
            models.Index(fields=["encoder"], name="ix_search_%(class)s_encoder"),
        ]

    def __str__(self) -> str:
        return f"{type(self).__name__} {self.content_id}/{self.encoder_id}"


class ClipEmbedding(Embedding):
    embedding = VectorField(dimensions=CLIP_DIMENSIONS)

    class Meta(Embedding.Meta):
        abstract = False


class Dinov2Embedding(Embedding):
    embedding = VectorField(dimensions=DINOV2_DIMENSIONS)

    class Meta(Embedding.Meta):
        abstract = False


class AnnIndexBuild(TimeStampedModel):
    """What an approximate index was built with, and how well it actually worked.

 An index without a recall measurement is a documented deficiency and not an acceptable state.
 The reason is specific to evidential retrieval: an approximate index can omit a true match, and
 to the investigator looking at the results a match that was not returned is indistinguishable
 from one that does not exist. Recording the parameters without the recall would document the
 configuration while leaving unstated the only thing anyone needs to know about it.

 So `measured_recall` is nullable, and null means the measurement has not been taken. It is a
 gap in the record rather than a default, which is why there is no default.
 """

    #: `search_clipembedding` or `search_dinov2embedding`, written from the model's `db_table` by
    #: the build task rather than typed. 63 characters because that is PostgreSQL's identifier
    #: limit, so the column cannot be too narrow for a name the database would accept.
    target_table = models.CharField(max_length=63)
    encoder = models.ForeignKey(Encoder, on_delete=models.PROTECT, related_name="index_builds")

    method = models.CharField(max_length=16, default="hnsw")
    #: No defaults on the three parameters. These are a record of what was used, and a default
    #: would let a build be recorded with values it was not built with.
    m = models.IntegerField()
    ef_construction = models.IntegerField()
    #: Set per session rather than baked into the index, so it is recorded as the operating point
    #: at which the recall below was measured. A recall figure without it means nothing.
    ef_search = models.IntegerField()

    row_count_at_build = models.BigIntegerField()
    built_at = models.DateTimeField()
    #: Informs the scheduling of the next build. Nullable because a build interrupted partway
    #: through has a row worth keeping and no duration to put in it.
    build_duration_seconds = models.IntegerField(null=True, blank=True)

    measured_recall = models.DecimalField(max_digits=5, decimal_places=4, null=True, blank=True)
    recall_k = models.IntegerField(null=True, blank=True)
    notes = models.TextField(default="", blank=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=Q(measured_recall__isnull=True)
                | (Q(measured_recall__gte=0) & Q(measured_recall__lte=1)),
                name="ck_search_annindexbuild_recall_range",
            ),
            # Paired both ways: a recall figure without the `k` it was measured at is not a
            # measurement, and a `k` without a figure records that someone meant to measure.
            models.CheckConstraint(
                condition=Q(measured_recall__isnull=True, recall_k__isnull=True)
                | Q(measured_recall__isnull=False, recall_k__isnull=False),
                name="ck_search_annindexbuild_recall_paired",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.method} on {self.target_table}"
