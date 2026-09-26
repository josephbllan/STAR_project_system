"""Centralised rather than per-app, so a test's category is visible from its path and a
security test cannot be quietly filed as a unit test ( 2).

The packages exist so that the same module name may appear in more than one layer:
`database/test_constraints.py` and `api/test_constraints.py` assert different things about the
same constraints, and both names are the right name.
"""
