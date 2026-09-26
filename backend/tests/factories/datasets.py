"""Assets, provenance and integrity.

Every factory here produces `synthetic` classification and a digest computed from a counter rather
than from bytes. That is deliberate on both counts: forbids real data on a development or
continuous-integration machine, and a digest derived from a sequence keeps the globally unique
constraint at out of the way of tests that are about something else.
"""

from __future__ import annotations

from uuid import uuid4

import factory

from apps.common import storage
from apps.datasets.models import (
    ArtifactKind,
    ContentObject,
    Corpus,
    DataClassification,
    DerivedArtifact,
    DigestVerification,
    EvidenceFile,
    EvidenceState,
    IntegrityState,
    Mount,
    VerificationOutcome,
)


def digest(n: int) -> str:
    """A well-formed but meaningless digest: 64 lower-case hexadecimal characters, unique per `n`.

 Not `hashlib.sha256(...)` of anything, because these bytes do not exist. A test that needs a
 digest to match its content computes it itself.
 """
    return f"{n:064x}"


class CorpusFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Corpus

    code = factory.Sequence(lambda n: f"Corpus{n}")
    name = factory.Sequence(lambda n: f"Test corpus {n}")
    data_classification = DataClassification.SYNTHETIC
    is_active = True


class MountFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Mount

    corpus = factory.SubFactory(CorpusFactory)
    path = factory.Sequence(lambda n: f"mounts/test-{n}")
    label = ""
    is_enabled = True


class ContentObjectFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = ContentObject

    # Declared here rather than left to the model default because the storage key is derived from
    # it: factory_boy resolves declarations before the instance exists, so a lazy attribute cannot
    # read a value the model constructor has not yet assigned.
    public_id = factory.LazyFunction(uuid4)
    sha256 = factory.Sequence(digest)
    byte_size = 4096
    media_type = "image/jpeg"
    width = 800
    height = 600
    # Through the real derivation, so that a test which then reads the object back exercises the
    # same key the ingestion pipeline would have written.
    storage_key = factory.LazyAttribute(lambda o: storage.storage_key_for_content(o.public_id))
    integrity_state = IntegrityState.UNVERIFIED


class EvidenceFileFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = EvidenceFile

    content = factory.SubFactory(ContentObjectFactory)
    corpus = factory.SubFactory(CorpusFactory)
    mount = None
    #: Shared-corpus evidence by default. A test that wants case-scoped evidence says so, because
    #: the two are governed by different authorisation paths.
    case = None
    original_filename = factory.Sequence(lambda n: f"trainer-{n}.jpg")
    source_path = ""
    state = EvidenceState.REGISTERED
    registered_by = None


class DerivedArtifactFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = DerivedArtifact

    public_id = factory.LazyFunction(uuid4)
    source_file = factory.SubFactory(EvidenceFileFactory)
    source_artifact = None
    kind = ArtifactKind.THUMBNAIL
    operation = "pillow.thumbnail"
    parameters = factory.LazyFunction(lambda: {"size": [256, 256]})
    sha256 = factory.Sequence(digest)
    byte_size = 2048
    media_type = "image/webp"
    storage_key = factory.LazyAttribute(
        lambda o: storage.storage_key_for_artifact(o.public_id, o.kind)
    )
    width = 256
    height = 192


class DigestVerificationFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = DigestVerification

    content = factory.SubFactory(ContentObjectFactory)
    expected_sha256 = factory.LazyAttribute(lambda o: o.content.sha256)
    observed_sha256 = factory.LazyAttribute(lambda o: o.content.sha256)
    outcome = VerificationOutcome.MATCH
    task_run = None
    detail = ""
