"""Evidence, runs, results and judgements outlive the application that wrote them.

Asserted from `shoerag_app`, for the same reason as the audit trail: a test connecting as the owner
can delete anything and would pass whatever the grants said.

What this protects against is not malice so much as a reasonable-looking line of code. "Clean up the
failed run" and "remove the duplicate registration" are both sentences someone will write, and both
destroy the record of what the system did. The database refusing them is the only version of this
control that survives a refactor.
"""

from __future__ import annotations

import importlib

import pytest
from django.conf import settings
from django.db import ProgrammingError, connection

pytestmark = [
    pytest.mark.integration,
    pytest.mark.django_db(databases=["default", "app"]),
]

#: Read from the migration rather than restated here. A table list copied into a test file is a list
#: that goes stale in one of the two places, and the one that goes stale is the test - which then
#: reports a control as present after it was narrowed. Imported by `importlib` because a module name
#: beginning with a digit cannot appear in an `import` statement.
_policy = importlib.import_module("apps.datasets.migrations.0003_evidential_revocations")
EVIDENTIAL_TABLES: tuple[str,...] = _policy.EVIDENTIAL_TABLES
READ_ONLY_TABLES: tuple[str,...] = _policy.READ_ONLY_TABLES


def privileges_on(table: str, role: str) -> set[str]:
    """Read on the owner's connection, where the grants are visible as data rather than as a
 refusal."""
    with connection.cursor() as cursor:
        cursor.execute(
            """
 SELECT privilege_type FROM information_schema.role_table_grants
 WHERE table_name = %s AND grantee = %s
 """, [table, role],
        )
        return {row[0] for row in cursor.fetchall()}


def test_the_policy_covers_the_tables_db_25_names() -> None:
    """Spelled out once, against the requirement, so that a table dropped from the migration's list
 fails here rather than passing silently through every parametrised test below."""
    assert set(EVIDENTIAL_TABLES) == {
        "datasets_contentobject",
        "datasets_evidencefile",
        "datasets_derivedartifact",
        "datasets_digestverification",
        "cases_case",
        "cases_run",
        "cases_query",
        "cases_result",
        "review_approval",
        "review_note",
        "review_rating",
        "reporting_report",
    }


@pytest.mark.parametrize("table", EVIDENTIAL_TABLES)
def test_the_application_role_cannot_delete_from_an_evidential_table(table: str) -> None:
    assert "DELETE" not in privileges_on(table, settings.DB_APP_ROLE)


@pytest.mark.parametrize("table", EVIDENTIAL_TABLES)
def test_the_application_role_can_still_read_and_write_an_evidential_table(table: str) -> None:
    """The revocation is narrow. Registering evidence, recording a run and writing a note are all
 ordinary work, and a control that blocked them would be replaced within a week by running the
 API as the owner."""
    privileges = privileges_on(table, settings.DB_APP_ROLE)
    assert {"SELECT", "INSERT"} <= privileges


@pytest.mark.parametrize("table", EVIDENTIAL_TABLES)
def test_no_evidential_table_can_be_truncated_by_the_application(table: str) -> None:
    """`TRUNCATE` is a separate privilege from `DELETE`. It was never granted, and this asserts the
 absence rather than revoking it a second time - which is the cheaper way to keep a negative
 true."""
    assert "TRUNCATE" not in privileges_on(table, settings.DB_APP_ROLE)


def test_a_result_cannot_be_updated_by_the_application() -> None:
    """A result is what the system found at a moment under a stated configuration. Code that
 needs to record a changed judgement writes a review record; it does not edit the finding."""
    assert "UPDATE" not in privileges_on("cases_result", settings.DB_APP_ROLE)


def test_only_results_are_read_only() -> None:
    """The other evidential tables remain updatable, and deliberately so: an evidence file changes
 state as it is verified, a run changes status as it progresses, an approval is decided and then
 countersigned. Immutability applies to the finding, not to everything near it."""
    assert READ_ONLY_TABLES == ("cases_result",)
    for table in set(EVIDENTIAL_TABLES) - set(READ_ONLY_TABLES):
        assert "UPDATE" in privileges_on(table, settings.DB_APP_ROLE), table


def test_a_delete_is_refused_by_the_database_and_not_by_the_orm(app_connection) -> None:  # noqa: ANN001
    """The distinction the whole migration exists for. An ORM-level guard is a code path; this is a
 refusal no amount of code can talk its way past.

 The statement matches no rows - this connection cannot see what the test arranged on `default` -
 and that is immaterial: PostgreSQL checks the privilege before it looks for rows, which is what
 makes the assertion independent of any arrangement.
 """
    with pytest.raises(ProgrammingError, match="permission denied"), app_connection.cursor() as c:
        c.execute("DELETE FROM datasets_evidencefile WHERE id = -1")


def test_an_update_to_a_result_is_refused_by_the_database(app_connection) -> None:  # noqa: ANN001
    with pytest.raises(ProgrammingError, match="permission denied"), app_connection.cursor() as c:
        c.execute("UPDATE cases_result SET score_fused = 1.0 WHERE id = -1")


def test_a_result_may_still_be_inserted(app_connection) -> None:  # noqa: ANN001
    """Revoking `UPDATE` must not have revoked the ability to record a finding in the first place.

 The row references a query and an evidence file that do not exist, because this connection
 cannot see what the test arranged on `default`. The statement still succeeds, and that is not an
 oversight: every foreign key in this schema is `NO ACTION DEFERRABLE INITIALLY DEFERRED`, so the
 violation is raised at commit rather than at the statement, and the fixture rolls back before
 then.

 Which makes the test slightly better than intended. It shows the privilege check passing, and it
 shows the deferral that `test_constraint_closure.py` asserts schema-wide behaving that way from
 the role the application actually uses.
 """
    with app_connection.cursor() as cursor:
        cursor.execute(
            """
 INSERT INTO cases_result
 (public_id, created_at, updated_at, query_id, rank, evidence_file_id,
 score_fused, score_model, score_clip)
 VALUES (gen_random_uuid, now, now, -1, 1, -1, 0.5, 0.5, 0.5)
 RETURNING id
 """
        )
        assert cursor.fetchone()[0] > 0


def test_the_owner_retains_deletion() -> None:
    """The honest position. A role that can run migrations can drop the table, so the separation is
 between the application and the schema owner rather than between the owner and the truth.
 Retention and lawful disposal are operated deliberately, by the owner, and recorded - not
 performed incidentally by a request."""
    with connection.cursor() as cursor:
        cursor.execute("SELECT has_table_privilege(current_user, 'cases_result', 'DELETE')")
        assert cursor.fetchone()[0] is True


def test_the_diagnostic_role_holds_none_of_this() -> None:
    for table in EVIDENTIAL_TABLES:
        assert privileges_on(table, settings.DB_READONLY_ROLE) == {"SELECT"}
