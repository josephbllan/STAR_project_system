"""Decide whether a probe is visible-spectrum or infrared.

Filenames are not identities here, so the decision is made from the bytes: a near-grey
image with low chromatic energy is routed as infrared.
"""

from __future__ import annotations

from enum import StrEnum


class Spectrum(StrEnum):
    VISIBLE = "visible"
    INFRARED = "infrared"


def route_image(data: bytes, *, width: int | None = None, height: int | None = None) -> Spectrum:
    """A cheap heuristic. Wrong on a grey visible photograph, which is acceptable: the
 alternative of running both encoders on every query doubles inference cost for a decision
    that only affects which corpus partition is searched, and those partitions are now a
    predicate.
 """
    if not data:
        return Spectrum.VISIBLE
    # Sample a stride of bytes. A real decode belongs in the worker, after bounds checks.
    sample = data[:: max(1, len(data) // 256)]
    if not sample:
        return Spectrum.VISIBLE
    mean = sum(sample) / len(sample)
    variance = sum((b - mean) ** 2 for b in sample) / len(sample)
    # Infrared captures often compress into a narrow band. This threshold is a starting point
    # and is recorded as such; the routed value is stored on the query so it can be reviewed.
    if variance < 80:
        return Spectrum.INFRARED
    return Spectrum.VISIBLE
