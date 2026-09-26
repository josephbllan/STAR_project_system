"""The `datasets` tables as PostgreSQL holds them.

Every constraint is exercised from both sides. A constraint tested only by its violation would
pass identically if it refused every row, which is a way of being wrong that a one-sided test
cannot see.

The centre of gravity here is : the digest is unique globally and the registration is unique
per corpus. Several tests exist only to pin that pair down, because the two halves are individually
plausible in the wrong combination and the consequence of getting it wrong - duplicated embeddings
that can silently diverge - would not show up until search results disagreed with each other.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from django.db import DataError, IntegrityError, connection, transaction
from django.db.models import ProtectedError

from apps.cases.models import Case, Result
from apps.common import storage
from apps.datasets.models import (
    MAX_PIXEL_DIMENSION,
    ArtifactKind,
    ContentObject,
    DataClassification,
    DerivedArtifact,
    EvidenceFile,
    EvidenceState,
    IntegrityState,
    VerificationOutcome,
)
from tests.factories.cases import CaseFactory
from tests.factories.datasets import (
    ContentObjectFactory,
    CorpusFactory,
    DerivedArtifactFactory,
    DigestVerificationFactory,
    EvidenceFileFactory,
    MountFactory,
    digest,
)
from tests.factories.tasks import TaskRunFactory

pytestmark = [pytest.mark.integration, pytest.mark.django_db]

NOW = datetime(2026, 1, 1, tzinfo=UTC)


def constraint_names(table: str) -> set[str]:
    with connection.cursor() as cursor:
        cursor.execute("SELECT conname FROM pg_constraint WHERE conrelid = %s::regclass", [table])
        return {row[0] for row in cursor.fetchall()}


def index_names(table: str) -> set[str]:
    with connection.cursor() as cursor:
        cursor.execute("SELECT indexname FROM pg_indexes WHERE tablename = %s", [table])
        return {row[0] for row in cursor.fetchall()}


# ----------------------------------------------------------------------------------------
# The names exist, and only the intended ones.
# ----------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("table", "expected"),
    [
        (
            "datasets_corpus",
            {
                "uq_datasets_corpus_code",
                "uq_datasets_corpus_name",
                "ck_datasets_corpus_classification_valid",
            },
        ),
        (
            "datasets_mount",
            {
                "uq_datasets_mount_path",
                "ck_datasets_mount_scan_status_valid",
                "ck_datasets_mount_scan_counts_non_negative",
            },
        ),
        (
            "datasets_contentobject",
            {
                "uq_datasets_contentobject_sha256",
                "uq_datasets_contentobject_storage_key",
                "uq_datasets_contentobject_public_id",
                "ck_datasets_contentobject_sha256_hex",
                "ck_datasets_contentobject_byte_size_positive",
                "ck_datasets_contentobject_dimensions_paired",
                "ck_datasets_contentobject_dimensions_bounded",
                "ck_datasets_contentobject_integrity_state_valid",
            },
        ),
        (
            "datasets_evidencefile",
            {
                "uq_datasets_evidencefile_corpus_content",
                "uq_datasets_evidencefile_public_id",
                "ck_datasets_evidencefile_state_valid",
            },
        ),
        (
            "datasets_derivedartifact",
            {
                "uq_datasets_derivedartifact_storage_key",
                "uq_datasets_derivedartifact_public_id",
                "ck_datasets_derivedartifact_kind_valid",
                "ck_datasets_derivedartifact_sha256_hex",
                "ck_datasets_derivedartifact_no_self_source",
            },
        ),
        (
            "datasets_digestverification",
            {
                "ck_datasets_digestverification_outcome_valid",
                "ck_datasets_digestverification_observed_present",
            },
        ),
    ],
)
def test_named_constraints_exist(table: str, expected: set[str]) -> None:
    assert expected <= constraint_names(table)


def test_the_parameter_digest_uniqueness_is_an_index_not_a_constraint() -> None:
    """`uq_datasets_derivedartifact_source_kind_params` is unique on an expression, which
 PostgreSQL can only express as a unique index. Asserted separately so its absence from
 `pg_constraint` reads as intended rather than as an omission."""
    assert "uq_datasets_derivedartifact_source_kind_params" in index_names(
        "datasets_derivedartifact"
    )


def test_no_redundant_foreign_key_index_was_left_behind() -> None:
    """Django indexes every foreign key. Where a named index already leads with that key the
 implicit one is suppressed, because two indexes on one leading column cost two writes per row
 and buy one read path - and these are the tables that grow."""
    suppressed = [
        ("datasets_mount", "corpus_id"),
        ("datasets_evidencefile", "content_id"),
        ("datasets_evidencefile", "corpus_id"),
        ("datasets_derivedartifact", "source_file_id"),
        ("datasets_digestverification", "content_id"),
    ]
    for table, column in suppressed:
        prefix = f"{table}_{column}_"
        leftover = {name for name in index_names(table) if name.startswith(prefix)}
        assert leftover == set, f"{table}.{column} still carries an implicit index"


# ----------------------------------------------------------------------------------------
# Corpus.
# ----------------------------------------------------------------------------------------


def test_corpus_code_is_unique() -> None:
    CorpusFactory(code="Ecom")
    with pytest.raises(IntegrityError), transaction.atomic():
        CorpusFactory(code="Ecom")


def test_corpus_name_is_unique() -> None:
    CorpusFactory(name="Retail catalogue")
    with pytest.raises(IntegrityError), transaction.atomic():
        CorpusFactory(name="Retail catalogue")


@pytest.mark.parametrize("classification", [c.value for c in DataClassification])
def test_both_classifications_are_accepted_by_the_schema(classification: str) -> None:
    """The restriction of an environment to one classification is configuration, not a constraint:
 the same schema serves both, and the serializer applies the environment's rule."""
    assert CorpusFactory(data_classification=classification).pk is not None


def test_a_third_classification_is_refused() -> None:
    with pytest.raises(IntegrityError), transaction.atomic():
        CorpusFactory(data_classification="confidential")


# ----------------------------------------------------------------------------------------
# Mount.
# ----------------------------------------------------------------------------------------


def test_mount_path_is_unique() -> None:
    MountFactory(path="corpora/ecom")
    with pytest.raises(IntegrityError), transaction.atomic():
        MountFactory(path="corpora/ecom")


def test_a_mount_that_has_never_been_scanned_has_no_status() -> None:
    """Null is the fourth state, and it is not a status. The scheduler has to tell "never scanned"
 from "scanned and failed", so an empty string would be a value with no meaning."""
    mount = MountFactory()
    assert mount.last_scan_status is None
    assert mount.last_scan_at is None


def test_an_unrecognised_scan_status_is_refused() -> None:
    with pytest.raises(IntegrityError), transaction.atomic():
        MountFactory(last_scan_status="in-progress")


@pytest.mark.parametrize(("seen", "errors"), [(-1, 0), (0, -1)], ids=["files_seen", "error_count"])
def test_scan_counts_cannot_be_negative(seen: int, errors: int) -> None:
    with pytest.raises(IntegrityError), transaction.atomic():
        MountFactory(last_scan_files_seen=seen, last_scan_error_count=errors)


def test_a_scan_records_the_task_run_that_performed_it() -> None:
    run = TaskRunFactory(task_name="datasets.scan_mount")
    mount = MountFactory(last_scan_task_run=run, last_scan_status="ok", last_scan_files_seen=12)
    assert mount.last_scan_task_run_id == run.pk


def test_protect_refuses_to_orphan_a_scan_record() -> None:
    run = TaskRunFactory()
    MountFactory(last_scan_task_run=run)
    with pytest.raises(ProtectedError):
        run.delete()


# ----------------------------------------------------------------------------------------
# Content: the half that is globally unique.
# ----------------------------------------------------------------------------------------


def test_the_digest_is_unique_globally() -> None:
    """. Not per corpus: the same bytes are one content object however many corpora register
 them, which is what keeps one embedding per encoder per byte sequence."""
    ContentObjectFactory(sha256=digest(1))
    with pytest.raises(IntegrityError), transaction.atomic():
        ContentObjectFactory(sha256=digest(1))


def test_the_storage_key_is_unique() -> None:
    """This is what prevents overwrite by construction rather than by care: a second row
 cannot claim a key that already holds bytes, and `storage.store` refuses to write over one."""
    first = ContentObjectFactory()
    with pytest.raises(IntegrityError), transaction.atomic():
        ContentObjectFactory(storage_key=first.storage_key)


def test_the_storage_key_is_derived_from_the_public_identifier() -> None:
    """Never from a filename. The invariant asserted is that the key a row holds is the one
 `storage_key_for_content` produces for its public identifier, because the signed-URL path
 reaches bytes by deriving the key from the identifier the API was given rather than by reading
 the column - and if the two ever disagreed, a request would silently be served the wrong
 object or none at all."""
    content = ContentObjectFactory()
    assert content.storage_key == storage.storage_key_for_content(content.public_id)


def test_content_records_no_filename() -> None:
    """asserted by absence. The same bytes may have arrived under several names and none of
 them is a property of the content, so the name belongs on the registration. An added
 `original_filename` column here would be the first step towards building a path from one."""
    columns = {field.name for field in ContentObject._meta.get_fields}
    assert "original_filename" not in columns
    assert "source_path" not in columns


@pytest.mark.parametrize(
    "value",
    ["0" * 63, "A" * 64, "g" * 64, " " * 64],
    ids=["too_short", "upper_case", "not_hexadecimal", "blank"],
)
def test_a_digest_that_is_not_a_digest_is_refused(value: str) -> None:
    """Upper case is in the list because it is the plausible failure: `hexdigest` is lower case
 but a digest pasted from another tool may not be, and two spellings of one digest would defeat
 the global uniqueness this table is built on."""
    with pytest.raises(IntegrityError), transaction.atomic():
        ContentObjectFactory(sha256=value)


def test_an_over_long_digest_is_refused_by_the_column_and_not_by_the_check() -> None:
    """Where each guarantee lives, since the two are easy to conflate. The column type caps the
 length, so an over-long value never reaches the check constraint and arrives as `DataError`
 rather than `IntegrityError` - which matters because the error handler maps constraint names
 and has nothing to map here."""
    with pytest.raises(DataError), transaction.atomic():
        ContentObjectFactory(sha256="0" * 65)


def test_zero_length_content_is_refused() -> None:
    with pytest.raises(IntegrityError), transaction.atomic():
        ContentObjectFactory(byte_size=0)


def test_content_before_decoding_has_neither_dimension() -> None:
    content = ContentObjectFactory(width=None, height=None)
    assert content.width is None
    content.refresh_from_db()
    assert content.pixel_count is None


@pytest.mark.parametrize(
    ("width", "height"), [(800, None), (None, 600)], ids=["height_missing", "width_missing"]
)
def test_one_dimension_without_the_other_is_refused(width: int | None, height: int | None) -> None:
    """A half-decoded row would leave `pixel_count` null while a dimension was known, which is
 exactly the state the decompression bound at cannot be applied to."""
    with pytest.raises(IntegrityError), transaction.atomic():
        ContentObjectFactory(width=width, height=height)


@pytest.mark.parametrize(
    ("width", "height"),
    [
        (0, 600),
        (800, 0),
        (MAX_PIXEL_DIMENSION + 1, 600),
        (800, MAX_PIXEL_DIMENSION + 1),
        (-1, -1),
    ],
    ids=["zero_width", "zero_height", "over_wide", "over_tall", "negative"],
)
def test_dimensions_outside_the_decompression_bound_are_refused(width: int, height: int) -> None:
    with pytest.raises(IntegrityError), transaction.atomic():
        ContentObjectFactory(width=width, height=height)


def test_dimensions_at_the_bound_are_accepted() -> None:
    """The bound is inclusive. Asserted because an off-by-one here would refuse legitimate
 evidence, and a refusal is harder to notice than an acceptance."""
    content = ContentObjectFactory(width=MAX_PIXEL_DIMENSION, height=1)
    assert content.pk is not None


def test_pixel_count_is_computed_by_the_database() -> None:
    """Generated rather than assigned, so it cannot disagree with the dimensions it comes from -
 including after an `UPDATE` that touches only one of them."""
    content = ContentObjectFactory(width=800, height=600)
    content.refresh_from_db()
    assert content.pixel_count == 480_000

    ContentObject.objects.filter(pk=content.pk).update(width=400)
    content.refresh_from_db()
    assert content.pixel_count == 240_000


def test_pixel_count_cannot_be_written_by_the_application() -> None:
    """`GENERATED ALWAYS... STORED`, so the column is not writable at all.

 Django discards the assignment rather than refusing it, which is the behaviour worth pinning:
 a service that believed it was correcting the value would see a successful update and no
 change. The invariant asserted is the outcome, not the mechanism.
 """
    content = ContentObjectFactory(width=800, height=600)
    ContentObject.objects.filter(pk=content.pk).update(pixel_count=1)

    content.refresh_from_db()
    assert content.pixel_count == 480_000


def test_an_unrecognised_integrity_state_is_refused() -> None:
    with pytest.raises(IntegrityError), transaction.atomic():
        ContentObjectFactory(integrity_state="suspicious")


@pytest.mark.parametrize("state", [s.value for s in IntegrityState])
def test_every_integrity_state_is_accepted(state: str) -> None:
    assert ContentObjectFactory(integrity_state=state).pk is not None


def test_new_content_is_unverified_rather_than_verified() -> None:
    """The default matters: content is trusted only once something has read the bytes back and
 agreed with the digest, and a default of `verified` would assert that on arrival."""
    assert ContentObjectFactory.integrity_state == IntegrityState.UNVERIFIED


# ----------------------------------------------------------------------------------------
# Registration: the half that is unique per corpus.
# ----------------------------------------------------------------------------------------


def test_the_same_bytes_register_once_per_corpus() -> None:
    first = EvidenceFileFactory()
    with pytest.raises(IntegrityError), transaction.atomic():
        EvidenceFileFactory(corpus=first.corpus, content=first.content)


def test_the_same_bytes_register_in_two_corpora() -> None:
    """The other side of, and the reason the uniqueness is not global. A photograph that is
 both in a retail catalogue and in a previous case is one content object with two
 registrations - not two copies, and therefore not two embeddings."""
    content = ContentObjectFactory()
    first = EvidenceFileFactory(content=content, corpus=CorpusFactory(code="Ecom"))
    second = EvidenceFileFactory(content=content, corpus=CorpusFactory(code="PreviousCases"))

    assert first.pk != second.pk
    assert content.registrations.count() == 2


def test_a_direct_upload_has_no_discovering_mount() -> None:
    assert EvidenceFileFactory(mount=None).pk is not None


def test_a_scan_registers_without_a_user() -> None:
    """Null `registered_by` is legal and means a scan rather than a person. Stated as a test so
 that a later attempt to make the column mandatory has to argue with it."""
    assert EvidenceFileFactory(registered_by=None).pk is not None


def test_an_unrecognised_registration_state_is_refused() -> None:
    with pytest.raises(IntegrityError), transaction.atomic():
        EvidenceFileFactory(state="deleted")


@pytest.mark.parametrize("state", [s.value for s in EvidenceState])
def test_every_registration_state_is_accepted(state: str) -> None:
    assert EvidenceFileFactory(state=state).pk is not None


def test_missing_is_a_state_and_not_a_deletion() -> None:
    """An unreadable file keeps its row, its digest and its history; retrieval
 discards the result. Removing the row instead would erase the record that it was ever there."""
    evidence = EvidenceFileFactory()
    evidence.state = EvidenceState.MISSING
    evidence.save(update_fields=["state", "state_changed_at", "updated_at"])

    evidence.refresh_from_db()
    assert evidence.state == EvidenceState.MISSING
    assert evidence.content_id is not None


def test_protect_refuses_to_delete_registered_content() -> None:
    """The immutability at is not a convention here. Content with a registration cannot be
 removed, so the chain from a result back to the bytes cannot be broken from either end."""
    evidence = EvidenceFileFactory()
    with pytest.raises(ProtectedError):
        evidence.content.delete()


def test_protect_refuses_to_delete_a_corpus_holding_evidence() -> None:
    evidence = EvidenceFileFactory()
    with pytest.raises(ProtectedError):
        evidence.corpus.delete()


# ----------------------------------------------------------------------------------------
# The column added by `datasets.0002`, once `cases` existed.
# ----------------------------------------------------------------------------------------


def test_shared_corpus_evidence_has_no_case() -> None:
    """Null is the discriminator for queryset scoping: shared content is visible to anyone
 with access to its corpus, case-scoped content only through the case."""
    assert EvidenceFileFactory.case_id is None


def test_case_scoped_evidence_records_its_case() -> None:
    case = CaseFactory()
    assert EvidenceFileFactory(case=case).case_id == case.pk


def test_protect_refuses_to_delete_a_case_holding_evidence() -> None:
    evidence = EvidenceFileFactory(case=CaseFactory())
    with pytest.raises(ProtectedError):
        evidence.case.delete()


def test_the_case_index_is_partial() -> None:
    """Most evidence in the shared corpora has no case, and those rows would otherwise be the bulk
 of the index while never being what it is consulted for."""
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT indexdef FROM pg_indexes WHERE indexname = %s",
            ["ix_datasets_evidencefile_case"],
        )
        (definition,) = cursor.fetchone

    assert "WHERE" in definition
    assert "case_id" in definition


def test_the_cycle_between_the_two_apps_is_real() -> None:
    """Why the column arrives in `datasets.0002` and not in `datasets.0001`. Asserted so that
 anyone tempted to fold the two migrations together meets the reason first."""
    evidence_case = EvidenceFile._meta.get_field("case").related_model
    result_evidence = Result._meta.get_field("evidence_file").related_model

    assert evidence_case is Case
    assert result_evidence is EvidenceFile


# ----------------------------------------------------------------------------------------
# Derived artefacts.
# ----------------------------------------------------------------------------------------


def test_the_kinds_agree_with_the_storage_module() -> None:
    """The kind becomes a path segment, so the enumeration and `storage.DERIVED_KINDS` have to be
 the same set. Asserted rather than trusted, because the two are edited in different files and
 a disagreement would surface as a rejected write during ingestion."""
    assert {k.value for k in ArtifactKind} == set(storage.DERIVED_KINDS)


def test_an_unrecognised_kind_is_refused() -> None:
    with pytest.raises(IntegrityError), transaction.atomic():
        DerivedArtifactFactory(kind="watermarked", storage_key="derived/x/ab/cd/ef")


@pytest.mark.parametrize("kind", [k.value for k in ArtifactKind])
def test_every_kind_is_accepted(kind: str) -> None:
    assert DerivedArtifactFactory(kind=kind).pk is not None


def test_derivation_with_the_same_parameters_cannot_produce_a_second_row() -> None:
    """The idempotency is in the schema rather than in the task, because a redelivered
 message reaches the database whatever the task believed about having run already."""
    first = DerivedArtifactFactory(parameters={"size": [256, 256]})
    with pytest.raises(IntegrityError), transaction.atomic():
        DerivedArtifactFactory(
            source_file=first.source_file, kind=first.kind, parameters={"size": [256, 256]}
        )


def test_key_order_in_the_parameters_does_not_make_two_rows() -> None:
    """The constraint digests the canonical text of the `jsonb` value, and PostgreSQL normalises
 key order on storage. Two objects that differ only in the order they were written are the same
 derivation, and a constraint that let them both through would defeat its own purpose."""
    first = DerivedArtifactFactory(parameters={"size": [256, 256], "quality": 80})
    with pytest.raises(IntegrityError), transaction.atomic():
        DerivedArtifactFactory(
            source_file=first.source_file,
            kind=first.kind,
            parameters={"quality": 80, "size": [256, 256]},
        )


def test_different_parameters_are_a_different_derivation() -> None:
    first = DerivedArtifactFactory(parameters={"size": [256, 256]})
    second = DerivedArtifactFactory(
        source_file=first.source_file, kind=first.kind, parameters={"size": [512, 512]}
    )
    assert second.pk != first.pk


def test_the_same_parameters_under_a_different_kind_are_a_different_derivation() -> None:
    first = DerivedArtifactFactory(kind=ArtifactKind.THUMBNAIL, parameters={"size": [256, 256]})
    second = DerivedArtifactFactory(
        source_file=first.source_file, kind=ArtifactKind.CROP, parameters={"size": [256, 256]}
    )
    assert second.pk != first.pk


def test_an_artefact_may_be_derived_from_another() -> None:
    """The relation is transitive, which is what makes the chain back to original evidence
 reconstructible for the audit at."""
    thumbnail = DerivedArtifactFactory(kind=ArtifactKind.CONVERSION)
    equalised = DerivedArtifactFactory(
        source_file=thumbnail.source_file,
        source_artifact=thumbnail,
        kind=ArtifactKind.EQUALISED,
        operation="cv2.equalizeHist",
    )
    assert equalised.source_artifact_id == thumbnail.pk
    assert thumbnail.derivatives.get == equalised


def test_an_artefact_cannot_be_its_own_source() -> None:
    artifact = DerivedArtifactFactory()
    with pytest.raises(IntegrityError), transaction.atomic():
        DerivedArtifact.objects.filter(pk=artifact.pk).update(source_artifact=artifact.pk)


def test_an_artefact_storage_key_is_unique() -> None:
    first = DerivedArtifactFactory()
    with pytest.raises(IntegrityError), transaction.atomic():
        DerivedArtifactFactory(storage_key=first.storage_key)


def test_an_artefact_digest_must_be_a_digest() -> None:
    with pytest.raises(IntegrityError), transaction.atomic():
        DerivedArtifactFactory(sha256="not-a-digest")


def test_protect_refuses_to_delete_the_source_of_an_artefact() -> None:
    artifact = DerivedArtifactFactory()
    with pytest.raises(ProtectedError):
        artifact.source_file.delete()


def test_an_artefact_records_the_task_run_that_produced_it() -> None:
    """Which artefact came out of which execution, so a defective encoder or a defective
 parameter set can be traced to everything it touched."""
    run = TaskRunFactory(task_name="datasets.derive_thumbnail")
    artifact = DerivedArtifactFactory(produced_by_task_run=run)
    assert artifact.produced_by_task_run_id == run.pk


# ----------------------------------------------------------------------------------------
# Digest verification.
# ----------------------------------------------------------------------------------------


def test_a_matching_verification_records_the_digest_it_observed() -> None:
    content = ContentObjectFactory()
    verification = DigestVerificationFactory(content=content)
    assert verification.expected_sha256 == verification.observed_sha256 == content.sha256


def test_an_unreadable_object_records_no_observed_digest() -> None:
    verification = DigestVerificationFactory(
        outcome=VerificationOutcome.UNREADABLE, observed_sha256=None
    )
    assert verification.observed_sha256 is None


def test_an_unreadable_outcome_with_an_observed_digest_is_refused() -> None:
    """Half the pairing at A row claiming the object could not be read while recording what
 was read is a row that cannot be believed in either direction."""
    with pytest.raises(IntegrityError), transaction.atomic():
        DigestVerificationFactory(outcome=VerificationOutcome.UNREADABLE)


@pytest.mark.parametrize("outcome", [VerificationOutcome.MATCH, VerificationOutcome.MISMATCH])
def test_a_readable_outcome_without_an_observed_digest_is_refused(outcome: str) -> None:
    with pytest.raises(IntegrityError), transaction.atomic():
        DigestVerificationFactory(outcome=outcome, observed_sha256=None)


def test_an_unrecognised_outcome_is_refused() -> None:
    with pytest.raises(IntegrityError), transaction.atomic():
        DigestVerificationFactory(outcome="inconclusive")


def test_a_mismatch_does_not_alter_the_stored_digest() -> None:
    """and the whole point of recording `expected_sha256` on the verification row. The
 stored digest is what the evidence was registered as; a mismatch is a fact about storage, not
 a correction to the record."""
    content = ContentObjectFactory(sha256=digest(7))
    DigestVerificationFactory(
        content=content,
        expected_sha256=digest(7),
        observed_sha256=digest(8),
        outcome=VerificationOutcome.MISMATCH,
    )

    content.refresh_from_db()
    assert content.sha256 == digest(7)


def test_verification_history_accumulates_rather_than_replaces() -> None:
    """A verification row is never deleted, including after a mismatch has been
 investigated and explained, because the history is part of the evidential account. `DELETE` is
 revoked on this table by migration at a later phase; this asserts the shape the revocation
 protects."""
    content = ContentObjectFactory()
    DigestVerificationFactory(content=content, verified_at=NOW)
    DigestVerificationFactory(
        content=content,
        observed_sha256=digest(99),
        outcome=VerificationOutcome.MISMATCH,
        verified_at=NOW.replace(month=2),
    )
    assert content.verifications.count() == 2


def test_protect_refuses_to_delete_verified_content() -> None:
    verification = DigestVerificationFactory()
    with pytest.raises(ProtectedError):
        verification.content.delete()
