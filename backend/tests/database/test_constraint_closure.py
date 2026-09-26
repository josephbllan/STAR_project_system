"""The closure tests: properties asserted over the whole schema rather than one table at a time.

`test_naming_convention.py` checks that what the models declare is well named, reaches the database
and has a registry entry. This module checks the two directions that file cannot:

- **Nothing is in the database that the models do not declare.** A constraint added by raw SQL in a
 migration is invisible to model inspection, and the first anyone hears of it is a 500.
- **Nothing is declared that no test exercises.** A constraint nobody has tried to violate is a
 claim, not a control. This is the property that makes the per-table suites complete by
 construction: adding a constraint without a test that violates it fails the build.

The rest are schema-wide invariants that would otherwise have to be restated per table: every table
has a primary key, every check constraint is validated, every foreign key has the delete rule Django
actually emits, and the table inventory is the one this project intends.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from django.apps import apps
from django.db import connection

pytestmark = [pytest.mark.integration, pytest.mark.django_db]

TESTS_ROOT = Path(__file__).resolve().parents[1]

#: Matches any identifier this project would recognise as a constraint or index name.
NAME_PATTERN = re.compile(r"\b(?:uq|ck|ix)_[a-z0-9_]+\b")


def application_tables() -> set[str]:
    return {
        model._meta.db_table
        for model in apps.get_models()
        if model._meta.app_config is not None and model._meta.app_config.name.startswith("apps.")
    }


def declared_constraints() -> set[str]:
    names: set[str] = set()
    for model in apps.get_models():
        if model._meta.app_config is None or not model._meta.app_config.name.startswith("apps."):
            continue
        names |= {constraint.name for constraint in model._meta.constraints}
    return names


def database_constraints(kinds: str = "uc") -> dict[str, str]:
    """Constraint name to table, for the application's own tables.

 `contype` is a single character: `p` primary key, `u` unique, `c` check, `f` foreign key.
 """
    tables = application_tables
    with connection.cursor() as cursor:
        cursor.execute(
            """
 SELECT c.conname, t.relname
 FROM pg_constraint c
 JOIN pg_class t ON t.oid = c.conrelid
 JOIN pg_namespace n ON n.oid = t.relnamespace
 WHERE n.nspname = 'public' AND c.contype = ANY(%s)
 """, [list(kinds)],
        )
        return {name: table for name, table in cursor.fetchall() if table in tables}


def names_written_in_tests() -> set[str]:
    found: set[str] = set()
    for path in TESTS_ROOT.rglob("*.py"):
        found |= set(NAME_PATTERN.findall(path.read_text(encoding="utf-8")))
    return found


def test_there_is_something_to_check() -> None:
    assert application_tables
    assert database_constraints


def test_every_constraint_in_the_database_is_declared_by_a_model() -> None:
    """The direction model inspection cannot see. A constraint created by `RunSQL` exists, can be
 violated, and would be dropped without comment by the next `makemigrations` that rewrites the
 table - so if one is ever needed, this test is where the exemption gets argued for."""
    undeclared = sorted(
        f"{table}.{name}"
        for name, table in database_constraints.items()
        if name not in declared_constraints and not name.endswith("_pkey")
    )
    assert not undeclared, (
        "present in the database but declared by no model, so no migration accounts for it: "
        f"{undeclared}"
    )


def test_every_constraint_is_exercised_by_at_least_one_test() -> None:
    """The property that makes the per-table suites complete rather than merely long.

 A constraint is exercised if its name appears somewhere under `tests/`. That is a weaker check
 than "a test violates it and asserts the error", which cannot be automated - but it is strong
 enough for the failure that matters: a constraint added and then never thought about again.
 """
    untested = sorted(set(database_constraints) - names_written_in_tests - {"_pkey"})
    untested = [name for name in untested if not name.endswith("_pkey")]
    assert not untested, (
        "these constraints are in the database and named in no test, so nothing has ever tried to "
        f"violate them: {untested}"
    )


def test_every_application_table_has_a_primary_key() -> None:
    """Not a formality. A table without one cannot be replicated logically, cannot be updated safely
 by a tool that identifies rows by key, and admits exact duplicates."""
    with connection.cursor() as cursor:
        cursor.execute(
            """
 SELECT t.relname
 FROM pg_class t
 JOIN pg_namespace n ON n.oid = t.relnamespace
 WHERE n.nspname = 'public' AND t.relkind = 'r'
 AND NOT EXISTS (
 SELECT 1 FROM pg_constraint c WHERE c.conrelid = t.oid AND c.contype = 'p'
 )
 """
        )
        without = {row[0] for row in cursor.fetchall()} & application_tables
    assert not without


def test_every_check_constraint_is_validated() -> None:
    """A constraint added `NOT VALID` applies to new rows only. Django never writes one, so this
 asserts that no migration has been hand-edited into doing so - which is a legitimate technique
 for a large table and a silent problem if the follow-up `VALIDATE` is forgotten."""
    with connection.cursor() as cursor:
        cursor.execute(
            """
 SELECT c.conname, t.relname
 FROM pg_constraint c
 JOIN pg_class t ON t.oid = c.conrelid
 WHERE c.contype IN ('c', 'f') AND NOT c.convalidated
 """
        )
        unvalidated = [f"{table}.{name}" for name, table in cursor.fetchall()]
    assert not unvalidated


def test_every_foreign_key_has_the_delete_rule_django_emits() -> None:
    """Recorded schema-wide because the first draft of claimed `ON DELETE RESTRICT`
 and four cascades, and neither is in the database.

 `on_delete` is a Python-level mechanism: Django emits no `ON DELETE` clause at all, so every
 foreign key here is `NO ACTION DEFERRABLE INITIALLY DEFERRED`. The consequence is asymmetric.
 `PROTECT` is still enforced, because `NO ACTION` refuses a delete that leaves a referencing
 row - it just refuses it at commit rather than at statement. `CASCADE` is not enforced at all: a
 raw SQL `DELETE` against a parent is refused rather than cascaded, and only a delete through the
 ORM removes the children.
 """
    tables = application_tables
    with connection.cursor() as cursor:
        cursor.execute(
            """
 SELECT c.conname, t.relname, c.confdeltype, c.condeferrable, c.condeferred
 FROM pg_constraint c
 JOIN pg_class t ON t.oid = c.conrelid
 JOIN pg_namespace n ON n.oid = t.relnamespace
 WHERE n.nspname = 'public' AND c.contype = 'f'
 """
        )
        rows = [row for row in cursor.fetchall() if row[1] in tables]

    assert rows, "no foreign keys found, so this test would pass on nothing"
    for name, table, delete_type, deferrable, deferred in rows:
        # 'a' is NO ACTION. 'r' would be RESTRICT, 'c' CASCADE, 'n' SET NULL, 'd' SET DEFAULT.
        assert delete_type == "a", f"{table}.{name} has an unexpected delete rule {delete_type!r}"
        assert deferrable and deferred, f"{table}.{name} is not deferred"


def test_the_only_deferred_unique_constraint_is_the_ordering_one() -> None:
    """One deferrable constraint in the whole schema, and it is deferrable for a specific reason:
 resequencing an ordering swaps two positions, and any single-statement order of those updates
 passes through a state where two rows share a position.

 Everything else is immediate on purpose. A deferred constraint reports its violation at commit,
 where the service that caused it is no longer on the stack and cannot handle it.
 """
    with connection.cursor() as cursor:
        cursor.execute(
            """
 SELECT c.conname
 FROM pg_constraint c
 JOIN pg_class t ON t.oid = c.conrelid
 WHERE c.contype IN ('u', 'c') AND c.condeferrable
 """
        )
        deferrable = {row[0] for row in cursor.fetchall()} & set(database_constraints)
    assert deferrable == {"uq_review_resultorder_case_position"}


def test_every_table_with_a_public_identifier_enforces_its_uniqueness() -> None:
    """The public identifier is what appears in a URL, and a duplicate would make two rows
 addressable by one path. The base class supplies the column; only a constraint makes it an
 identifier."""
    missing = []
    for model in apps.get_models():
        if model._meta.app_config is None or not model._meta.app_config.name.startswith("apps."):
            continue
        if "public_id" not in {field.name for field in model._meta.fields}:
            continue
        expected = f"uq_{model._meta.app_label}_{model._meta.model_name}_public_id"
        if expected not in database_constraints:
            missing.append(f"{model._meta.label} expected {expected}")
    assert not missing


def test_the_table_inventory_is_the_one_this_project_intends() -> None:
    """A deliberate tripwire rather than documentation. Adding a table is a decision about what the
 system stores, and this test makes that decision visible in a diff instead of arriving as a
 migration nobody read.

 `datasets_evidencefile` is the table the `datasets`/`cases` cycle is broken around: it is
 created without its case reference in `datasets.0001` and gains it in `datasets.0002`, because
 two apps cannot each depend on the other's initial migration.
 """
    assert application_tables == {
        "accounts_user",
        "tasks_taskrun",
        "datasets_corpus",
        "datasets_mount",
        "datasets_contentobject",
        "datasets_evidencefile",
        "datasets_derivedartifact",
        "datasets_digestverification",
        "search_encoder",
        "search_clipembedding",
        "search_dinov2embedding",
        "search_annindexbuild",
        "cases_case",
        "cases_casemembership",
        "cases_run",
        "cases_runcorpus",
        "cases_query",
        "cases_result",
        "review_rating",
        "review_tag",
        "review_tagassignment",
        "review_note",
        "review_resultorder",
        "review_approval",
        "reporting_report",
        "audit_auditevent",
        "config_setting",
    }
