"""L2 normalisation, and the rejection of the zero vector.

Lives in the retrieval package rather than the model layer: this is the half of the
system under the framework-independence contract, and the half whose numerical behaviour
is under test. A model `save` that normalised would put arithmetic somewhere no numerical
test looks.

Pure Python by choice. `numpy` belongs to the encoder dependencies, which are several gigabytes
and are not installed before the encoders are; the cost of a Python loop is irrelevant beside
the forward pass that produced the vector.
"""

from __future__ import annotations

import math
from collections.abc import Sequence

#: Below this the vector is treated as zero. A magnitude this small is either an encoder that
#: failed or arithmetic that underflowed, and dividing by it produces numbers that look like
#: results.
ZERO_MAGNITUDE_TOLERANCE = 1e-12


class DegenerateVectorError(ValueError):
    """The vector cannot be normalised, so no row may be written for it.

    A failed encode may return a zero vector so that one unreadable image does not end a
    batch. That behaviour is kept at the batch level, but the sentinel is never persisted:
    a zero vector in an index matches everything equally and is indistinguishable from a
    legitimate result.
    """


def magnitude(values: Sequence[float]) -> float:
    return math.sqrt(math.fsum(value * value for value in values))


def l2_normalise(values: Sequence[float]) -> tuple[float,...]:
    """Returns the unit vector in the same direction.

 Raises `DegenerateVectorError` on an empty or zero-magnitude input rather than returning
 something storable, because the caller's next action would otherwise be a write.
 """
    if not values:
        raise DegenerateVectorError("An empty vector has no direction")
    length = magnitude(values)
    if length < ZERO_MAGNITUDE_TOLERANCE:
        raise DegenerateVectorError(
            f"Vector magnitude {length!r} is indistinguishable from zero; no row may be written"
        )
    return tuple(value / length for value in values)


def is_normalised(values: Sequence[float], *, tolerance: float = 1e-6) -> bool:
    """Whether the vector is already of unit length.

 Used by the assertion that every persisted vector arrived normalised. The tolerance
 is loose enough for float32 storage, which is what pgvector holds, and tight enough that an
 un-normalised vector fails.
 """
    if not values:
        return False
    return abs(magnitude(values) - 1.0) <= tolerance
