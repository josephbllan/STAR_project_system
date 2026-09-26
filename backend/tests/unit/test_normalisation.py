"""L2 normalisation, and the refusal to produce a storable zero vector.

Pure functions, no framework, hand-computed expectations. This is the layer `import-linter`
makes possible: `retrieval` imports no Django, so these tests need no database ( 5.1).
"""

import math

import pytest

from retrieval.normalisation import DegenerateVectorError, is_normalised, l2_normalise, magnitude

pytestmark = pytest.mark.unit


def test_unit_vector_is_unchanged() -> None:
    assert l2_normalise([1.0, 0.0, 0.0]) == (1.0, 0.0, 0.0)


def test_normalisation_against_hand_computed_value() -> None:
    """(3, 4) has magnitude 5, so the unit vector is (0.6, 0.8). Chosen because the expected
 answer is exact in binary floating point and can be written down without the function."""
    assert l2_normalise([3.0, 4.0]) == (0.6, 0.8)


def test_result_has_unit_magnitude() -> None:
    normalised = l2_normalise([2.0, -7.0, 0.5, 13.0])
    assert math.isclose(magnitude(normalised), 1.0, abs_tol=1e-12)


def test_direction_is_preserved() -> None:
    """Normalisation must scale, never reorder or re-sign. A sign error here would invert the
 meaning of every cosine similarity computed afterwards."""
    normalised = l2_normalise([3.0, -4.0])
    assert normalised[0] > 0
    assert normalised[1] < 0
    assert math.isclose(normalised[0] / normalised[1], 3.0 / -4.0)


def test_zero_vector_is_refused() -> None:
    """A zero vector on encoder failure must never be persisted: in an index it matches
    everything equally and is indistinguishable from a legitimate result."""
    with pytest.raises(DegenerateVectorError, match="indistinguishable from zero"):
        l2_normalise([0.0, 0.0, 0.0])


def test_vector_below_tolerance_is_refused() -> None:
    """Underflow is the same failure as an explicit zero, and dividing by it would produce
 numbers that look like results."""
    with pytest.raises(DegenerateVectorError):
        l2_normalise([1e-20, 1e-20])


def test_empty_vector_is_refused() -> None:
    with pytest.raises(DegenerateVectorError, match="no direction"):
        l2_normalise([])


def test_is_normalised_accepts_float32_rounding() -> None:
    """pgvector stores float32, so a vector read back is not bit-identical to the one written.
 The check has to tolerate that and still reject a vector that was never normalised."""
    assert is_normalised([0.6, 0.8000001])
    assert not is_normalised([3.0, 4.0])
    assert not is_normalised([])
