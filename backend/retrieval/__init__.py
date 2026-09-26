"""Retrieval, deliberately framework-free.

Nothing here may import Django, DRF, Celery or `apps`. The `retrieval-is-framework-free`
contract in `pyproject.toml` enforces it. Encoders, fusion and the query router do not
know what is calling them.

Plain dataclasses cross this boundary in both directions. No ORM instance enters, and no
`QuerySet` leaves.
"""
