"""Encoders and vectors.

The vectors these factories produce go through `retrieval.normalisation.l2_normalise`, which is
where normalisation belongs. A factory that wrote an arbitrary vector would let a test pass
while storing something the retrieval query cannot rank, and the assertion that every persisted
vector is of unit length would then be testing the factory rather than the system.
"""

from __future__ import annotations

import factory
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
from retrieval.normalisation import l2_normalise
from tests.factories.datasets import ContentObjectFactory


def unit_vector(dimensions: int, *, seed: int = 1) -> list[float]:
    """A deterministic unit vector of the given width.

 Deterministic rather than random because a failing vector test should fail the same way twice,
 and `pytest-randomly` reseeds every run. The components vary across the dimensions so that a
 transposition or a truncation shows up as a different vector rather than as the same one.
 """
    raw = [float((index * seed) % 17 + 1) for index in range(dimensions)]
    return list(l2_normalise(raw))


class EncoderFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Encoder

    name = "openai/clip-vit-base-patch32"
    family = EncoderFamily.CLIP
    version = factory.Sequence(lambda n: f"1.{n}")
    preprocess_version = "p1"
    # Derived rather than stated, so a factory can switch family without tripping
    # `ck_search_encoder_dimensions_match_family` on the way.
    dimensions = factory.LazyAttribute(
        lambda o: CLIP_DIMENSIONS if o.family == EncoderFamily.CLIP else DINOV2_DIMENSIONS
    )
    is_active = True


class Dinov2EncoderFactory(EncoderFactory):
    name = "vit_small_patch14_dinov2.lvd142m"
    family = EncoderFamily.DINOV2


class ClipEmbeddingFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = ClipEmbedding

    content = factory.SubFactory(ContentObjectFactory)
    encoder = factory.SubFactory(EncoderFactory)
    embedding = factory.LazyFunction(lambda: unit_vector(CLIP_DIMENSIONS))
    task_run = None


class Dinov2EmbeddingFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Dinov2Embedding

    content = factory.SubFactory(ContentObjectFactory)
    encoder = factory.SubFactory(Dinov2EncoderFactory)
    embedding = factory.LazyFunction(lambda: unit_vector(DINOV2_DIMENSIONS))
    task_run = None


class AnnIndexBuildFactory(factory.django.DjangoModelFactory):
    """Defaults to a build with **no** recall measurement, because that is the state a build is in
 the moment it finishes and the state calls a documented deficiency. A test that wants a
 measured build says so."""

    class Meta:
        model = AnnIndexBuild

    target_table = ClipEmbedding._meta.db_table
    encoder = factory.SubFactory(EncoderFactory)
    method = "hnsw"
    m = 16
    ef_construction = 64
    ef_search = 40
    row_count_at_build = 100_000
    built_at = factory.LazyFunction(timezone.now)
    build_duration_seconds = 900
    measured_recall = None
    recall_k = None
    notes = ""
