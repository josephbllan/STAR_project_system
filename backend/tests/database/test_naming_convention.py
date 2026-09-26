"""The naming convention of, asserted against the database rather than the models.

`models.E034` is silenced in the settings because its 30-character limit is Oracle's and this
project is PostgreSQL only. That decision is safe only if something else enforces the limit that
does apply, which is what this module is for.

The failure being prevented is quiet. PostgreSQL truncates an identifier at 63 characters
without error, so a name declared at 70 arrives in `pg_constraint` at 63. The registry lookup in
`apps/common/exceptions.py` then misses, the violation falls through to a 500, and the symptom
appears far from the cause.
"""

from __future__ import annotations

import pytest
from django.apps import apps
from django.db import connection

pytestmark = [pytest.mark.integration, pytest.mark.django_db]

#: PostgreSQL's `NAMEDATALEN - 1`. Exceeding it truncates silently.
MAX_IDENTIFIER_LENGTH = 63

VALID_PREFIXES = ("pk_", "fk_", "uq_", "ck_", "ix_")


def application_models() -> list[type]:
    """Only this project's models. Django's own tables do not follow this convention and are not
 ours to rename."""
    return [
        model
        for model in apps.get_models()
        if model._meta.app_config is not None and model._meta.app_config.name.startswith("apps.")
    ]


def declared_names() -> list[tuple[str, str]]:
    names: list[tuple[str, str]] = []
    for model in application_models:
        label = model._meta.label
        names.extend((label, constraint.name) for constraint in model._meta.constraints)
        names.extend((label, index.name) for index in model._meta.indexes)
    return names


def test_there_is_something_to_check() -> None:
    """Without this, every assertion below would pass on an empty list and the module would look
 like coverage while providing none."""
    assert declared_names


def test_no_declared_name_exceeds_the_postgresql_limit() -> None:
    for label, name in declared_names:
        assert len(name) <= MAX_IDENTIFIER_LENGTH, (
            f"{label}.{name} is {len(name)} characters; PostgreSQL would truncate it to "
            f"{MAX_IDENTIFIER_LENGTH} and the 409 mapping would then miss it"
        )


def test_every_name_follows_the_kind_app_model_intent_convention() -> None:
    """A name without its kind prefix tells a reader nothing about what was violated."""
    for label, name in declared_names:
        assert name.startswith(VALID_PREFIXES), f"{label}.{name} has no recognised kind prefix"
        app_label = label.split(".")[0].lower()
        assert name.split("_")[1] == app_label, (
            f"{label}.{name} does not name its own app, so a violation cannot be located from it"
        )


def test_declared_names_reach_the_database_intact() -> None:
    """The assertion that closes the loop. A name declared in a model but absent from the
 database means the migration was never generated or never applied, and no amount of model
 inspection would reveal it."""
    with connection.cursor() as cursor:
        cursor.execute("SELECT conname FROM pg_constraint")
        in_database = {row[0] for row in cursor.fetchall()}
        cursor.execute("SELECT indexname FROM pg_indexes WHERE schemaname = 'public'")
        in_database |= {row[0] for row in cursor.fetchall()}

    missing = [f"{label}.{name}" for label, name in declared_names if name not in in_database]
    assert not missing, f"declared but not present in the database: {missing}"


def test_every_named_constraint_has_a_registry_entry() -> None:
    """. A constraint added without deciding what the client should be told falls through
 to a 500, which is precisely the failure the registry exists to prevent. Completeness is
 therefore a build property rather than an intention.

 Indexes are excluded: an index is not something a request can violate.
 """
    from apps.common.exceptions import CONSTRAINT_REGISTRY

    unmapped = sorted(
        name
        for _, name in declared_names
        if name.startswith(("uq_", "ck_")) and name not in CONSTRAINT_REGISTRY
    )
    assert not unmapped, (
        "these constraints can be violated by a request and have no registry entry, so they "
        f"would reach the client as a 500: {unmapped}"
    )
