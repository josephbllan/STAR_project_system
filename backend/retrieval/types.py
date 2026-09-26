"""The plain types that cross the boundary between retrieval and the rest of the system.

Every field is a primitive, a UUID, or another type declared here. Nothing carries a database
identity, because a value that knew its own row would give the framework-free half of the
system a reason to import the framework.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from uuid import UUID


class Modality(StrEnum):
    """What was encoded. A query may be either; a corpus item is always an image."""

    IMAGE = "image"
    TEXT = "text"


@dataclass(frozen=True, slots=True)
class EncoderRef:
    """Which model produced a vector, at which dimensionality.

 Both are carried because a vector is meaningless without them: comparing a 512-dimension
 CLIP vector against a 384-dimension DINOv2 vector is not a weaker result, it is an error.
 """

    name: str
    dimensions: int


@dataclass(frozen=True, slots=True)
class Embedding:
    """One vector, L2-normalised, with the content it describes.

 `content_id` is the opaque public identifier of a content object, never a primary key. It
 identifies bytes rather than a registration, because a vector is a property of the bytes
 and re-registering the same bytes elsewhere does not produce a second vector.
 """

    content_id: UUID
    encoder: EncoderRef
    modality: Modality
    values: tuple[float,...]


@dataclass(frozen=True, slots=True)
class ScoredCandidate:
    """One result from one encoder, before fusion.

 `score` is cosine similarity in [-1, 1]. It is not a distance and not a percentage; the
 conversion for display belongs to the presentation layer, which is on the far side of this
 boundary.
 """

    content_id: UUID
    encoder: EncoderRef
    score: float


@dataclass(frozen=True, slots=True)
class FusedResult:
    """One result after fusion, retaining the components it was computed from.

 The components are kept rather than discarded because a reviewer must be able to see that
 a high fused score is not the product of a single encoder.
 """

    content_id: UUID
    fused_score: float
    components: dict[str, float] = field(default_factory=dict)
