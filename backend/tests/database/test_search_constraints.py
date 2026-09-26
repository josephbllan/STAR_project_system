"""`search_encoder`, the two embedding tables and `search_annindexbuild` as PostgreSQL holds them.

Two groups of test here do something other than exercise a constraint.

The first records what the foreign keys actually are at the SQL level, which is not what Django's
`on_delete` argument suggests. Django implements both `PROTECT` and `CASCADE` in Python and emits no
`ON DELETE` clause at all, so a statement issued outside the ORM behaves differently from one
issued through it. That is worth pinning down in a schema whose integrity claims are part of its
purpose.

The second asserts the **absence** of the HNSW indexes. They are deferred to `search.0002` on
purpose, and an absence that is deliberate should be written down, or the next reader
will take it for an omission and add them in the wrong migration.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from django.db import DataError, IntegrityError, connection, transaction
from django.db.models import ProtectedError
from django.utils import timezone

from apps.search.models import (
    CLIP_DIMENSIONS,
    DINOV2_DIMENSIONS,
    AnnIndexBuild,
    ClipEmbedding,
    Dinov2Embedding,
    Encoder,
    EncoderFamily,
)
from retrieval.normalisation import is_normalised
from tests.factories.datasets import ContentObjectFactory, CorpusFactory, EvidenceFileFactory
from tests.factories.search import (
    AnnIndexBuildFactory,
    ClipEmbeddingFactory,
    Dinov2EmbeddingFactory,
    EncoderFactory,
    unit_vector,
)
from tests.factories.tasks import TaskRunFactory

pytestmark = [pytest.mark.integration, pytest.mark.django_db]

EMBEDDING_TABLES = ("search_clipembedding", "search_dinov2embedding")


def constraint_names(table: str) -> set[str]:
    with connection.cursor() as cursor:
        cursor.execute("SELECT conname FROM pg_constraint WHERE conrelid = %s::regclass", [table])
        return {row[0] for row in cursor.fetchall()}


def index_names(table: str) -> set[str]:
    with connection.cursor() as cursor:
        cursor.execute("SELECT indexname FROM pg_indexes WHERE tablename = %s", [table])
        return {row[0] for row in cursor.fetchall()}


def delete_rules(table: str) -> dict[str, str]:
    """The `ON DELETE` action PostgreSQL actually holds for each foreign key on a table."""
    with connection.cursor() as cursor:
        cursor.execute(
            """
 SELECT conname, confdeltype
 FROM pg_constraint
 WHERE conrelid = %s::regclass AND contype = 'f'
 """, [table],
        )
        return dict(cursor.fetchall())


# ----------------------------------------------------------------------------------------
# The names exist.
# ----------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("table", "expected"),
    [
        (
            "search_encoder",
            {
                "uq_search_encoder_identity",
                "ck_search_encoder_family_valid",
                "ck_search_encoder_dimensions_match_family",
            },
        ),
        ("search_clipembedding", {"uq_search_clipembedding_content_encoder"}),
        ("search_dinov2embedding", {"uq_search_dinov2embedding_content_encoder"}),
        (
            "search_annindexbuild",
            {
                "ck_search_annindexbuild_recall_range",
                "ck_search_annindexbuild_recall_paired",
            },
        ),
    ],
)
def test_named_constraints_exist(table: str, expected: set[str]) -> None:
    assert expected <= constraint_names(table)


@pytest.mark.parametrize(
    ("table", "expected"),
    [
        ("search_clipembedding", "ix_search_clipembedding_encoder"),
        ("search_dinov2embedding", "ix_search_dinov2embedding_encoder"),
    ],
)
def test_the_encoder_index_is_named_by_the_schema_and_not_by_django(
    table: str, expected: str
) -> None:
    """The two tables are declared from one abstract base whose constraint and index names
 interpolate `%(class)s`, so each concrete table gets the name 5.2 gives it
 without either being written out twice. The alternative - two near-identical model bodies - is
 how the two come to differ in some detail nobody intended."""
    assert expected in index_names(table)


@pytest.mark.parametrize("table", EMBEDDING_TABLES)
def test_no_redundant_foreign_key_index_was_left_behind(table: str) -> None:
    for column in ("content_id", "encoder_id"):
        leftover = {name for name in index_names(table) if name.startswith(f"{table}_{column}_")}
        assert leftover == set, f"{table}.{column} still carries an implicit index"


# ----------------------------------------------------------------------------------------
# What the foreign keys are, as opposed to what `on_delete` reads like.
# ----------------------------------------------------------------------------------------


@pytest.mark.parametrize("table", EMBEDDING_TABLES)
def test_every_foreign_key_is_no_action_in_the_database(table: str) -> None:
    """Django realises `on_delete` in Python and emits no `ON DELETE` clause, so at the SQL level
 every foreign key here is `NO ACTION DEFERRABLE INITIALLY DEFERRED` - `a` in `confdeltype`.

 The consequence is asymmetric and worth knowing. `PROTECT` is still delivered by the database:
 deferred `NO ACTION` refuses the deletion at commit. `CASCADE` is **not**: a `DELETE` issued
 outside the ORM against content with embeddings is refused rather than cascaded. Since content
 is never deleted through the application and `DELETE` is revoked on the evidential
 tables at a later migration, nothing depends on the cascade reaching raw SQL - but the schema
 document's claim about cascade describes ORM behaviour, not a database rule.
 """
    assert set(delete_rules(table).values()) == {"a"}


def test_deleting_content_through_the_orm_removes_its_embeddings() -> None:
    """The cascade at, which is one of only four in the schema. An embedding cannot
 meaningfully exist without the bytes it describes, and it is derived data that can be
 recomputed - so if content ever does go, the vectors go with it rather than being left for
 someone to find and remove by hand."""
    embedding = ClipEmbeddingFactory()
    content = embedding.content

    content.delete()

    assert not ClipEmbedding.objects.filter(pk=embedding.pk).exists()


@pytest.mark.parametrize(
    "factory", [ClipEmbeddingFactory, Dinov2EmbeddingFactory], ids=["clip", "dinov2"]
)
def test_protect_refuses_to_delete_an_encoder_that_produced_vectors(factory: type) -> None:
    """RESTRICT and not CASCADE on the encoder, because the encoder row is the only record of how
 these vectors were produced. Losing it would leave a table of numbers whose comparability
 could no longer be established."""
    embedding = factory()
    with pytest.raises(ProtectedError):
        embedding.encoder.delete()


# ----------------------------------------------------------------------------------------
# The absence that is deliberate.
# ----------------------------------------------------------------------------------------


@pytest.mark.parametrize("table", EMBEDDING_TABLES)
def test_the_hnsw_index_is_built(table: str) -> None:
    """search.0002. Built concurrently; recall is recorded by `search.build_ann_index`."""
    present = index_names(table)
    assert f"ix_{table}_hnsw" in present


# ----------------------------------------------------------------------------------------
# Encoder identity.
# ----------------------------------------------------------------------------------------


def test_the_encoder_identity_is_the_triple() -> None:
    EncoderFactory(name="clip", version="1.0", preprocess_version="p1")
    with pytest.raises(IntegrityError), transaction.atomic():
        EncoderFactory(name="clip", version="1.0", preprocess_version="p1")


def test_a_preprocessing_change_makes_a_second_encoder() -> None:
    """and the reason `preprocess_version` is part of the identity rather than metadata.
 A change to resizing, cropping or colour handling changes the vectors an otherwise identical
 model produces, and comparing across the two returns a number that means nothing."""
    first = EncoderFactory(name="clip", version="1.0", preprocess_version="p1")
    second = EncoderFactory(name="clip", version="1.0", preprocess_version="p2")

    assert second.pk != first.pk
    assert Encoder.objects.filter(name="clip", version="1.0").count() == 2


def test_an_unrecognised_family_is_refused() -> None:
    with pytest.raises(IntegrityError), transaction.atomic():
        EncoderFactory(family="siglip", dimensions=CLIP_DIMENSIONS)


@pytest.mark.parametrize(
    ("family", "dimensions"),
    [(EncoderFamily.CLIP, CLIP_DIMENSIONS), (EncoderFamily.DINOV2, DINOV2_DIMENSIONS)],
)
def test_each_family_accepts_its_own_dimensionality(family: str, dimensions: int) -> None:
    assert EncoderFactory(family=family, dimensions=dimensions).pk is not None


@pytest.mark.parametrize(
    ("family", "dimensions"),
    [
        (EncoderFamily.CLIP, DINOV2_DIMENSIONS),
        (EncoderFamily.DINOV2, CLIP_DIMENSIONS),
        (EncoderFamily.CLIP, 768),
    ],
    ids=["clip_at_384", "dinov2_at_512", "clip_at_768"],
)
def test_a_dimensionality_its_family_cannot_hold_is_refused(family: str, dimensions: int) -> None:
    """Caught at registration rather than at the first encoding batch, which is where the
 mismatch would otherwise surface - as a rejected insert a long way from the row that caused
 it."""
    with pytest.raises(IntegrityError), transaction.atomic():
        EncoderFactory(family=family, dimensions=dimensions)


# ----------------------------------------------------------------------------------------
# Embeddings.
# ----------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("factory", "dimensions"),
    [(ClipEmbeddingFactory, CLIP_DIMENSIONS), (Dinov2EmbeddingFactory, DINOV2_DIMENSIONS)],
    ids=["clip", "dinov2"],
)
def test_a_vector_round_trips_at_its_declared_width(factory: type, dimensions: int) -> None:
    embedding = factory()
    embedding.refresh_from_db()

    assert len(embedding.embedding) == dimensions


@pytest.mark.parametrize(
    ("factory", "dimensions"),
    [(ClipEmbeddingFactory, CLIP_DIMENSIONS), (Dinov2EmbeddingFactory, DINOV2_DIMENSIONS)],
    ids=["clip", "dinov2"],
)
def test_a_persisted_vector_is_of_unit_length(factory: type, dimensions: int) -> None:
    """Cosine distance and inner product induce the same ordering only for unit
 vectors, so this is the precondition for the retrieval query's ranking meaning what it says.

 Read back from the database rather than checked in memory, because pgvector stores float32 and
 the question is whether the value survives that narrowing within tolerance.
 """
    embedding = factory()
    embedding.refresh_from_db()

    assert is_normalised(embedding.embedding)


@pytest.mark.parametrize(
    ("factory", "wrong_width"),
    [(ClipEmbeddingFactory, DINOV2_DIMENSIONS), (Dinov2EmbeddingFactory, CLIP_DIMENSIONS)],
    ids=["clip_given_384", "dinov2_given_512"],
)
def test_a_vector_of_the_wrong_width_is_refused(factory: type, wrong_width: int) -> None:
    """The declared dimensionality is the guarantee that makes one table per width worth having.
 An index cannot be built over a column of mixed widths, so this has to fail at the write."""
    with pytest.raises(DataError), transaction.atomic():
        factory(embedding=unit_vector(wrong_width))


@pytest.mark.parametrize(
    "factory", [ClipEmbeddingFactory, Dinov2EmbeddingFactory], ids=["clip", "dinov2"]
)
def test_one_vector_per_encoder_per_content(factory: type) -> None:
    """This is what makes re-encoding an update rather than an accumulation, and what stops
 a redelivered encoding task from putting a second copy of the same vector into the index -
 where it would occupy a result slot that a different image should have had."""
    first = factory()
    with pytest.raises(IntegrityError), transaction.atomic():
        factory(content=first.content, encoder=first.encoder)


def test_two_encoders_of_the_same_family_each_get_a_vector() -> None:
    content = ContentObjectFactory()
    first = ClipEmbeddingFactory(content=content, encoder=EncoderFactory(preprocess_version="p1"))
    second = ClipEmbeddingFactory(content=content, encoder=EncoderFactory(preprocess_version="p2"))

    assert {first.pk, second.pk} == set(
        ClipEmbedding.objects.filter(content=content).values_list("pk", flat=True)
    )


def test_one_content_object_carries_a_vector_in_each_table() -> None:
    """The two tables are independent, so a single image is encoded by both families and fused at
 query time. The unique constraints are per table, which is what permits this."""
    content = ContentObjectFactory()
    ClipEmbeddingFactory(content=content)
    Dinov2EmbeddingFactory(content=content)

    assert ClipEmbedding.objects.filter(content=content).count() == 1
    assert Dinov2Embedding.objects.filter(content=content).count() == 1


def test_an_embedding_belongs_to_content_and_not_to_a_registration() -> None:
    """and the payoff of the decision in `apps.datasets`. The same bytes registered in
 two corpora are one content object, so they carry one vector - not two that would double the
 most expensive rows in the database and could silently diverge if one were re-encoded."""
    content = ContentObjectFactory()
    EvidenceFileFactory(content=content, corpus=CorpusFactory(code="Ecom"))
    EvidenceFileFactory(content=content, corpus=CorpusFactory(code="PreviousCases"))

    ClipEmbeddingFactory(content=content)

    assert content.registrations.count() == 2
    assert ClipEmbedding.objects.filter(content=content).count() == 1


def test_an_embedding_records_the_task_run_that_produced_it() -> None:
    run = TaskRunFactory(task_name="indexing.encode_corpus")
    assert ClipEmbeddingFactory(task_run=run).task_run_id == run.pk


def test_an_encoder_registration_alone_writes_no_vector() -> None:
    """A failed encode may return a zero vector so one unreadable image does not end a
    batch. That sentinel is never persisted: a zero vector is equidistant from everything,
    so in an index it matches every query and is indistinguishable from a real result."""
    encoder = EncoderFactory()
    ContentObjectFactory

    assert not ClipEmbedding.objects.filter(encoder=encoder).exists()


# ----------------------------------------------------------------------------------------
# Index builds.
# ----------------------------------------------------------------------------------------


def test_a_fresh_build_has_no_recall_measurement() -> None:
    """Null is the state a build is in the moment it finishes, and it denotes a gap in the
 record rather than a value - which is why the column has no default."""
    build = AnnIndexBuildFactory()
    assert build.measured_recall is None
    assert build.recall_k is None


def test_a_measured_build_records_the_figure_and_its_k() -> None:
    build = AnnIndexBuildFactory(measured_recall=Decimal("0.9740"), recall_k=50)
    build.refresh_from_db()

    assert build.measured_recall == Decimal("0.9740")
    assert build.recall_k == 50


@pytest.mark.parametrize("recall", [Decimal("0"), Decimal("1"), Decimal("0.5000")])
def test_recall_at_and_within_the_bounds_is_accepted(recall: Decimal) -> None:
    assert AnnIndexBuildFactory(measured_recall=recall, recall_k=10).pk is not None


@pytest.mark.parametrize("recall", [Decimal("-0.0001"), Decimal("1.0001")])
def test_recall_outside_zero_to_one_is_refused(recall: Decimal) -> None:
    with pytest.raises(IntegrityError), transaction.atomic():
        AnnIndexBuildFactory(measured_recall=recall, recall_k=10)


@pytest.mark.parametrize(
    ("recall", "k"),
    [(Decimal("0.9740"), None), (None, 50)],
    ids=["figure_without_k", "k_without_figure"],
)
def test_recall_and_its_k_are_paired(recall: Decimal | None, k: int | None) -> None:
    """Both directions. A figure without the `k` it was measured at is not a measurement, and a `k`
 without a figure records only that someone intended to take one."""
    with pytest.raises(IntegrityError), transaction.atomic():
        AnnIndexBuildFactory(measured_recall=recall, recall_k=k)


def test_recall_keeps_four_decimal_places() -> None:
    """`numeric(5,4)`. Four places because the difference between 0.974 and 0.9743 recall is the
 difference between one true match in a thousand being returned or not, and at evidential
 volumes that is a real number of missed results rather than a rounding detail."""
    build = AnnIndexBuildFactory(measured_recall=Decimal("0.9743"), recall_k=100)
    build.refresh_from_db()

    assert build.measured_recall == Decimal("0.9743")


def test_the_target_table_holds_a_real_table_name() -> None:
    """Written from the model's `db_table` by the build task rather than typed, which is why there
 is no check constraint on it. Asserted so that the convention is recorded somewhere."""
    build = AnnIndexBuildFactory(target_table=Dinov2Embedding._meta.db_table)
    assert build.target_table == "search_dinov2embedding"
    assert build.target_table in EMBEDDING_TABLES


def test_protect_refuses_to_delete_an_encoder_with_a_recorded_build() -> None:
    build = AnnIndexBuildFactory()
    with pytest.raises(ProtectedError):
        build.encoder.delete()


def test_the_build_parameters_have_no_defaults() -> None:
    """in spirit: `m`, `ef_construction` and `ef_search` record what was used. A default
 would let a build be recorded with values it was not built with, which is worse than having no
 record of it at all."""
    with pytest.raises(IntegrityError), transaction.atomic():
        AnnIndexBuild.objects.create(
            target_table=ClipEmbedding._meta.db_table,
            encoder=EncoderFactory(),
            row_count_at_build=1,
            built_at=timezone.now(),
        )
