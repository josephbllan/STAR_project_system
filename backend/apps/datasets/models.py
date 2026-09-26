"""Assets, provenance and integrity.

A content digest is unique globally; registration into a corpus is a separate row.

* `ContentObject` is one row per distinct byte sequence. It owns the bytes, the storage key
 and the integrity state, and it records no filename.
* `EvidenceFile` is a registration of that content into a corpus. The same photograph may
 appear in a catalogue and in a case as two registrations of one object, not two copies.

Embeddings hang off content, never off registration. `EvidenceFile.case_id` is added in
`datasets.0002` because that foreign key closes a cycle with `cases`.
"""

from __future__ import annotations

from django.conf import settings
from django.db import models
from django.db.models import F, Q
from django.db.models.functions import MD5, Cast, Now
from django.utils import timezone

from apps.common.models import PublicIdModel, TimeStampedModel
from apps.common.querysets import ScopedManager

#: Lower-case hexadecimal SHA-256. Held as text rather than `bytea` because every diagnostic
#: path - logs, reports, comparison against an external tool - presents it as text, and a
#: conversion at each of those boundaries costs more than sixteen bytes per row.
#:
#: writes `char(64)`. This is `varchar(64)`, which in PostgreSQL is identical in
#: storage and avoids the blank padding `char` applies on read - padding that would make a digest
#: compare unequal to itself after a round trip through a client that strips whitespace. Nothing
#: is lost by the change: the check constraint pins the length at exactly 64 characters, which is
#: the only guarantee `char(64)` was there to give.
SHA256_PATTERN = r"^[0-9a-f]{64}$"
SHA256_LENGTH = 64

#: A bound applied before decoding, so that a compressed file claiming enormous
#: dimensions is refused rather than decompressed. Thirty thousand pixels on a side is far
#: beyond any forensic photograph and far below what exhausts memory.
MAX_PIXEL_DIMENSION = 30_000

# Django indexes every foreign key by default. Where a named index already leads with that key,
# leading column is that key, the implicit one is redundant: two indexes on the same leading
# column cost two writes per row and buy one read path. Those are suppressed with
# `db_index=False`, and the suppression is named at each site. Where no named index leads with the
# key, the implicit index is kept - the join still has to be served.


class DataClassification(models.TextChoices):
    """Which imagery a deployment will accept.

 A deployment declares which classifications it permits, and a corpus whose classification is
 not permitted cannot be created there. Development and continuous integration permit
 `synthetic` only, which makes this the control that binds the machine the system is built on.
 It reaches vectors automatically, because a vector arrives only through content registered to
 a corpus.
 """

    REAL = "real", "Real"
    SYNTHETIC = "synthetic", "Synthetic"


class ScanStatus(models.TextChoices):
    OK = "ok", "Ok"
    PARTIAL = "partial", "Partial"
    FAILED = "failed", "Failed"


class IntegrityState(models.TextChoices):
    UNVERIFIED = "unverified", "Unverified"
    VERIFIED = "verified", "Verified"
    MISMATCH = "mismatch", "Mismatch"
    UNREADABLE = "unreadable", "Unreadable"


class EvidenceState(models.TextChoices):
    REGISTERED = "registered", "Registered"
    INDEXED = "indexed", "Indexed"
    #: The underlying object could not be read. A state and not a deletion, which is what lets
    #: retrieval discard a result whose file is absent without removing the record.
    MISSING = "missing", "Missing"
    QUARANTINED = "quarantined", "Quarantined"


class ArtifactKind(models.TextChoices):
    """Must stay in step with `apps.common.storage.DERIVED_KINDS`, since the kind becomes a path
 segment. A test asserts the two sets are equal rather than trusting that both were edited."""

    THUMBNAIL = "thumbnail", "Thumbnail"
    CROP = "crop", "Crop"
    FRAME = "frame", "Frame"
    CONVERSION = "conversion", "Conversion"
    EQUALISED = "equalised", "Equalised"


class VerificationOutcome(models.TextChoices):
    MATCH = "match", "Match"
    MISMATCH = "mismatch", "Mismatch"
    UNREADABLE = "unreadable", "Unreadable"


class Corpus(TimeStampedModel):
    """A named collection, for example Ecom, NDFsim or PreviousCases."""

    code = models.CharField(max_length=50)
    name = models.CharField(max_length=200)
    description = models.TextField(default="", blank=True)
    data_classification = models.CharField(max_length=16, choices=DataClassification.choices)
    is_active = models.BooleanField(default=True)

    class Meta:
        verbose_name_plural = "corpora"
        constraints = [
            models.UniqueConstraint(fields=["code"], name="uq_datasets_corpus_code"),
            models.UniqueConstraint(fields=["name"], name="uq_datasets_corpus_name"),
            models.CheckConstraint(
                condition=Q(data_classification__in=[c.value for c in DataClassification]),
                name="ck_datasets_corpus_classification_valid",
            ),
        ]

    def __str__(self) -> str:
        return self.code


class Mount(TimeStampedModel):
    """A storage prefix to be scanned, including last-scan health fields.

    Restricting an environment to one data classification is configuration rather than a
    constraint, because the same schema serves both. The check that a corpus's classification
    is permitted is applied at the serializer.
    """

    # Covered by `ix_datasets_mount_corpus_enabled`. That index is partial, so a lookup of the
    # disabled mounts of a corpus scans - acceptable here and nowhere else in this app, because
    # mounts number in the tens and the only such lookup is the reference check on corpus delete.
    corpus = models.ForeignKey(
        Corpus, on_delete=models.PROTECT, related_name="mounts", db_index=False
    )
    #: A storage prefix and never a client-supplied value. Nothing here is passed to the
    #: filesystem directly: `apps/common/storage.py` is the only module that opens a file.
    path = models.CharField(max_length=1024)
    label = models.CharField(max_length=200, default="", blank=True)
    is_enabled = models.BooleanField(default=True)

    last_scan_at = models.DateTimeField(null=True, blank=True)
    # DJ001: null here means "never scanned", which the scan scheduler must distinguish from a
    # scan that completed. An empty string would be a fourth, meaningless status value.
    last_scan_status = models.CharField(  # noqa: DJ001
        max_length=16, choices=ScanStatus.choices, null=True, blank=True
    )
    last_scan_files_seen = models.IntegerField(default=0)
    last_scan_error_count = models.IntegerField(default=0)
    last_scan_task_run = models.ForeignKey(
        "tasks.TaskRun",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="mount_scans",
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["path"], name="uq_datasets_mount_path"),
            models.CheckConstraint(
                condition=Q(last_scan_status__isnull=True)
                | Q(last_scan_status__in=[s.value for s in ScanStatus]),
                name="ck_datasets_mount_scan_status_valid",
            ),
            models.CheckConstraint(
                condition=Q(last_scan_files_seen__gte=0) & Q(last_scan_error_count__gte=0),
                name="ck_datasets_mount_scan_counts_non_negative",
            ),
        ]
        indexes = [
            # Partial: the scan scheduler only ever asks for enabled mounts, and a disabled mount
            # should not cost anything to skip.
            models.Index(
                fields=["corpus"],
                condition=Q(is_enabled=True),
                name="ix_datasets_mount_corpus_enabled",
            ),
        ]

    def __str__(self) -> str:
        return self.path


class ContentObject(TimeStampedModel, PublicIdModel):
    """One row per distinct byte sequence, identified by its digest.

 It records no filename. The filename lives on the registration, because the same bytes may
 have arrived under different names and none of them is a property of the content.

 Original evidence is immutable. No column other than the verification triple -
 `last_verified_at`, `integrity_state`, and the dimensions filled in on first decode - is
 written after creation, and no code path amends the digest, the size or the storage key.
 """

    sha256 = models.CharField(max_length=SHA256_LENGTH)
    byte_size = models.BigIntegerField()
    #: Determined by inspecting the bytes, never taken from the client's declaration.
    media_type = models.CharField(max_length=100)

    width = models.IntegerField(null=True, blank=True)
    height = models.IntegerField(null=True, blank=True)

    #: Derived from `public_id` by `apps.common.storage.storage_key_for_content`, never from any
    #: client-supplied value. Unique, which prevents overwrite by construction.
    storage_key = models.CharField(max_length=512)

    #: Generated by the database rather than by the application, so it cannot disagree with the
    #: dimensions it is computed from. It bounds the decompression check at.
    pixel_count = models.GeneratedField(
        expression=F("width") * F("height"),
        output_field=models.IntegerField(null=True),
        db_persist=True,
    )

    #: Denormalised from the verification history purely so the sweep can order by it without a
    #: join. The history remains authoritative.
    last_verified_at = models.DateTimeField(null=True, blank=True)
    integrity_state = models.CharField(
        max_length=16, choices=IntegrityState.choices, default=IntegrityState.UNVERIFIED
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["sha256"], name="uq_datasets_contentobject_sha256"),
            models.UniqueConstraint(
                fields=["storage_key"], name="uq_datasets_contentobject_storage_key"
            ),
            models.UniqueConstraint(
                fields=["public_id"], name="uq_datasets_contentobject_public_id"
            ),
            # A digest that is not a digest is a defect worth failing on: every downstream
            # comparison against an external tool assumes this character class.
            models.CheckConstraint(
                condition=Q(sha256__regex=SHA256_PATTERN),
                name="ck_datasets_contentobject_sha256_hex",
            ),
            models.CheckConstraint(
                condition=Q(byte_size__gt=0), name="ck_datasets_contentobject_byte_size_positive"
            ),
            # `(width IS NULL) = (height IS NULL)`. One dimension without the other is a
            # half-decoded row, and `pixel_count` would be null while a dimension was known.
            models.CheckConstraint(
                condition=Q(width__isnull=True, height__isnull=True)
                | Q(width__isnull=False, height__isnull=False),
                name="ck_datasets_contentobject_dimensions_paired",
            ),
            models.CheckConstraint(
                condition=Q(width__isnull=True)
                | (
                    Q(width__gte=1)
                    & Q(width__lte=MAX_PIXEL_DIMENSION)
                    & Q(height__gte=1)
                    & Q(height__lte=MAX_PIXEL_DIMENSION)
                ),
                name="ck_datasets_contentobject_dimensions_bounded",
            ),
            models.CheckConstraint(
                condition=Q(integrity_state__in=[s.value for s in IntegrityState]),
                name="ck_datasets_contentobject_integrity_state_valid",
            ),
        ]
        indexes = [
            # Nulls first, because content never verified is the most overdue. Mismatches are
            # excluded: a mismatch is not re-swept, it is investigated.
            models.Index(
                F("last_verified_at").asc(nulls_first=True),
                condition=~Q(integrity_state=IntegrityState.MISMATCH),
                name="ix_datasets_contentobject_verification_due",
            ),
        ]

    def __str__(self) -> str:
        return self.sha256[:12]


class EvidenceFile(TimeStampedModel, PublicIdModel):
    """Registration of content into a corpus.

 This is what the API addresses and what a result points at, because the corpus and the case
 context are what an investigator is shown - a bare content object would be a set of bytes
 with no provenance.
 """

    # Covered by `ix_datasets_evidencefile_content` and `ix_datasets_evidencefile_corpus_state`.
    content = models.ForeignKey(
        ContentObject, on_delete=models.PROTECT, related_name="registrations", db_index=False
    )
    corpus = models.ForeignKey(
        Corpus, on_delete=models.PROTECT, related_name="evidence_files", db_index=False
    )
    #: Null for a direct upload, which has no discovering mount.
    mount = models.ForeignKey(
        Mount, on_delete=models.PROTECT, null=True, blank=True, related_name="evidence_files"
    )
    #: Populated for case-scoped evidence and null for a shared corpus, which is what makes the
    #: column the discriminator for queryset scoping: shared content is visible to anyone
    #: with access to the corpus, case-scoped content only through the case.
    #:
    #: Added by `datasets.0002` rather than here, because the reference closes a cycle -
    #: `cases_case` is referenced by this column while `cases_result` references this table - and
    #: neither app can be created whole before the other.
    case = models.ForeignKey(
        "cases.Case",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="evidence_files",
        db_index=False,
    )

    #: Provenance only. Never used to build a path, an identifier, or a response header without
    #: escaping.
    original_filename = models.CharField(max_length=512, default="", blank=True)
    #: Where a mount scan found the file. Provenance only, for the same reason.
    source_path = models.CharField(max_length=1024, default="", blank=True)

    state = models.CharField(
        max_length=16, choices=EvidenceState.choices, default=EvidenceState.REGISTERED
    )

    #: Null for a scan, which registers content without a human requesting each file.
    registered_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="registered_evidence",
    )
    registered_at = models.DateTimeField(default=timezone.now, db_default=Now())
    state_changed_at = models.DateTimeField(default=timezone.now, db_default=Now())

    # The one scoped model in the project where a null case means *shared* rather than orphaned, and
    # the exception is load-bearing: evidence registered to a shared corpus belongs to no case, and
    # without it no account could search the shared corpora at all.
    objects = ScopedManager(case_paths=("case",), shared_when_unscoped=True)

    class Meta:
        constraints = [
            # The same bytes register once per corpus. Not globally, because the corpus is
            # part of the registration's meaning.
            models.UniqueConstraint(
                fields=["corpus", "content"], name="uq_datasets_evidencefile_corpus_content"
            ),
            models.UniqueConstraint(
                fields=["public_id"], name="uq_datasets_evidencefile_public_id"
            ),
            models.CheckConstraint(
                condition=Q(state__in=[s.value for s in EvidenceState]),
                name="ck_datasets_evidencefile_state_valid",
            ),
        ]
        indexes = [
            # The join from an embedding to its registrations, executed on every search. This is
            # the index that decision made necessary.
            models.Index(fields=["content"], name="ix_datasets_evidencefile_content"),
            # Corpus restriction with absent files excluded, and indexing coverage.
            models.Index(fields=["corpus", "state"], name="ix_datasets_evidencefile_corpus_state"),
            # Partial: most evidence in the shared corpora has no case, and those rows would
            # otherwise be the bulk of the index while never being what it is consulted for.
            models.Index(
                fields=["case"],
                condition=Q(case__isnull=False),
                name="ix_datasets_evidencefile_case",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.corpus_id}/{self.content_id}"


class DerivedArtifact(TimeStampedModel, PublicIdModel):
    """Everything produced from evidence: thumbnails, crops, frames, conversions, equalisations.

 The relation is transitive - an artefact may itself be the source of another - so the chain
    back to original evidence is reconstructible. Deeper
 cycles are prevented by construction rather than by constraint, artefacts being append-only:
 a row can only reference one that already exists.
 """

    # Covered by `ix_datasets_derivedartifact_source_kind`.
    source_file = models.ForeignKey(
        EvidenceFile, on_delete=models.PROTECT, related_name="derived_artifacts", db_index=False
    )
    #: Set where this was derived from another artefact rather than from the registration.
    source_artifact = models.ForeignKey(
        "self", on_delete=models.PROTECT, null=True, blank=True, related_name="derivatives"
    )

    kind = models.CharField(max_length=24, choices=ArtifactKind.choices)
    #: The named operation that produced it, so the artefact records how and not only that.
    operation = models.CharField(max_length=100)
    #: Genuinely variable across operations, which is the case `jsonb` is for rather than a
    #: column per parameter of every operation anyone might add.
    parameters = models.JSONField(default=dict)

    sha256 = models.CharField(max_length=SHA256_LENGTH)
    byte_size = models.BigIntegerField()
    media_type = models.CharField(max_length=100)
    storage_key = models.CharField(max_length=512)
    width = models.IntegerField(null=True, blank=True)
    height = models.IntegerField(null=True, blank=True)

    produced_by_task_run = models.ForeignKey(
        "tasks.TaskRun",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="derived_artifacts",
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["storage_key"], name="uq_datasets_derivedartifact_storage_key"
            ),
            models.UniqueConstraint(
                fields=["public_id"], name="uq_datasets_derivedartifact_public_id"
            ),
            # Idempotency of derivation: re-running thumbnail generation with the same
            # parameters cannot produce a second row. The digest of the parameters is used rather
            # than the parameters themselves because `jsonb` has no default btree ordering and
            # key order would otherwise make two equivalent objects distinct.
            models.UniqueConstraint(
                F("source_file"),
                F("kind"),
                MD5(Cast("parameters", output_field=models.TextField())),
                name="uq_datasets_derivedartifact_source_kind_params",
            ),
            models.CheckConstraint(
                condition=Q(kind__in=[k.value for k in ArtifactKind]),
                name="ck_datasets_derivedartifact_kind_valid",
            ),
            models.CheckConstraint(
                condition=Q(sha256__regex=SHA256_PATTERN),
                name="ck_datasets_derivedartifact_sha256_hex",
            ),
            models.CheckConstraint(
                condition=Q(source_artifact__isnull=True) | ~Q(source_artifact=F("id")),
                name="ck_datasets_derivedartifact_no_self_source",
            ),
        ]
        indexes = [
            # Thumbnail lookup during result presentation: the query issued for every row of
            # every result page, and therefore the one whose N+1 behaviour is asserted.
            models.Index(
                fields=["source_file", "kind"], name="ix_datasets_derivedartifact_source_kind"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.kind} of {self.source_file_id}"


class DigestVerification(TimeStampedModel):
    """Each re-verification of a content object.

 A row is never deleted, including after a mismatch has been investigated and explained: the
 history of verification is part of the evidential account. `DELETE` is revoked on this
 table by migration rather than left to convention.

 A mismatch never updates the stored digest. It quarantines the registration, raises an alert
 and produces an audit event, because a mismatch means either that evidence has been altered or
 that storage has failed, and those require different human responses.
 """

    # Covered by `ix_datasets_digestverification_content_time`.
    content = models.ForeignKey(
        ContentObject, on_delete=models.PROTECT, related_name="verifications", db_index=False
    )
    verified_at = models.DateTimeField(default=timezone.now, db_default=Now())

    #: The digest as stored at the time of verification, recorded rather than joined so the row
    #: remains meaningful if the content row is later corrected by a migration.
    expected_sha256 = models.CharField(max_length=SHA256_LENGTH)
    #: Null exactly where the object could not be read, which the paired check enforces.
    observed_sha256 = models.CharField(  # noqa: DJ001
        max_length=SHA256_LENGTH, null=True, blank=True
    )

    outcome = models.CharField(max_length=16, choices=VerificationOutcome.choices)
    task_run = models.ForeignKey(
        "tasks.TaskRun",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="digest_verifications",
    )
    #: Redacted. No evidential content, no path, no credential.
    detail = models.TextField(default="", blank=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=Q(outcome__in=[o.value for o in VerificationOutcome]),
                name="ck_datasets_digestverification_outcome_valid",
            ),
            # `(outcome = 'unreadable') = (observed_sha256 IS NULL)`. Enforced in both directions
            # because either half alone permits a row that claims to have read something it did
            # not, or to have failed to read something it did.
            models.CheckConstraint(
                condition=Q(outcome=VerificationOutcome.UNREADABLE, observed_sha256__isnull=True)
                | (~Q(outcome=VerificationOutcome.UNREADABLE) & Q(observed_sha256__isnull=False)),
                name="ck_datasets_digestverification_observed_present",
            ),
        ]
        indexes = [
            models.Index(
                fields=["content", "-verified_at"],
                name="ix_datasets_digestverification_content_time",
            ),
            # Partial: the integrity alert asks only about failures, and they are rare enough
            # that the index stays small while the history grows.
            models.Index(
                fields=["-verified_at"],
                condition=~Q(outcome=VerificationOutcome.MATCH),
                name="ix_datasets_digestverification_failures",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.outcome} at {self.verified_at:%Y-%m-%d}"
