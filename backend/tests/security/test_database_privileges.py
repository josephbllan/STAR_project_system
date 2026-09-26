"""Privilege separation, asserted from the role that is restricted.

Every other test in this project connects as `shoerag_owner`, because the runner has to create and
drop the test database. The owner can do everything, so an append-only assertion made on that
connection would pass no matter what the grants said. These tests use `app_connection`, which is
authenticated as `shoerag_app` - the role the API and the workers actually use.

What is being checked is not that the application avoids rewriting the audit trail. It is that it
*cannot*, and that the refusal comes from PostgreSQL rather than from a code path someone could
change or bypass.
"""

from __future__ import annotations

import pytest
from django.conf import settings
from django.db import ProgrammingError, connection

from apps.audit.models import AuditAction, AuditOutcome

pytestmark = [
    pytest.mark.integration,
    pytest.mark.django_db(databases=["default", "app"]),
]

AUDIT_TABLE = "audit_auditevent"

#: Written out rather than interpolated from `AUDIT_TABLE`. A table name in an f-string is
#: indistinguishable from an injection vector to a linter, and arguing with the linter here would
#: mean suppressing the rule in a file whose whole subject is what the application is not allowed to
#: do.
INSERT_EVENT = """
 INSERT INTO audit_auditevent
 (public_id, occurred_at, action, outcome, actor_username, actor_role, target_type,
 target_label, correlation_id, request_method, request_path, detail)
 VALUES (gen_random_uuid, now, %s, %s, 'system', '', 'corpus', '',
 gen_random_uuid, '', '', '{}')
 RETURNING id
"""


def insert_event(app_connection) -> int:  # noqa: ANN001
    """One event, written through the restricted connection.

 Written by this connection rather than arranged through a factory, because the factory writes on
 `default` inside an uncommitted transaction that this connection cannot see.
 """
    with app_connection.cursor() as cursor:
        cursor.execute(INSERT_EVENT, [AuditAction.EVIDENCE_ACCESSED, AuditOutcome.SUCCEEDED])
        return cursor.fetchone()[0]


def privileges_on(table: str, role: str) -> set[str]:
    """Read from `information_schema` on the owner's connection, which is where the grants are
 visible as data rather than as a refusal."""
    with connection.cursor() as cursor:
        cursor.execute(
            """
 SELECT privilege_type FROM information_schema.role_table_grants
 WHERE table_name = %s AND grantee = %s
 """, [table, role],
        )
        return {row[0] for row in cursor.fetchall()}


def test_the_application_role_is_a_different_role_from_the_owner() -> None:
    """The precondition for everything below. If the two connections resolved to the same role, then
 every test in this module would pass while proving nothing."""
    with connection.cursor() as cursor:
        cursor.execute("SELECT current_user")
        owner = cursor.fetchone()[0]
    assert owner != settings.DB_APP_ROLE


def test_the_application_connection_is_the_application_role(app_connection) -> None:  # noqa: ANN001
    with app_connection.cursor() as cursor:
        cursor.execute("SELECT current_user")
        assert cursor.fetchone()[0] == settings.DB_APP_ROLE


def test_the_application_role_can_record_an_audit_event(app_connection) -> None:  # noqa: ANN001
    """Stated first, because a revocation that also removed the ability to record would be a
 catastrophic success: no tampering, and no trail either."""
    assert insert_event(app_connection) > 0


def test_the_application_role_cannot_rewrite_an_audit_event(app_connection) -> None:  # noqa: ANN001
    """The row it tries to change is one it wrote itself a moment earlier, so the refusal is
 about the privilege and not about visibility."""
    event_id = insert_event(app_connection)
    with pytest.raises(ProgrammingError, match="permission denied"), app_connection.cursor() as c:
        c.execute("UPDATE audit_auditevent SET outcome = 'failed' WHERE id = %s", [event_id])


def test_the_application_role_cannot_delete_an_audit_event(app_connection) -> None:  # noqa: ANN001
    event_id = insert_event(app_connection)
    with pytest.raises(ProgrammingError, match="permission denied"), app_connection.cursor() as c:
        c.execute("DELETE FROM audit_auditevent WHERE id = %s", [event_id])


def test_the_application_role_cannot_truncate_the_audit_trail(app_connection) -> None:  # noqa: ANN001
    """`TRUNCATE` is a separate privilege from `DELETE` in PostgreSQL. A role holding it can empty
 the trail in one statement without holding `DELETE` at all, which is exactly the gap that turns
 a control into theatre."""
    with pytest.raises(ProgrammingError, match="permission denied"), app_connection.cursor() as c:
        c.execute("TRUNCATE audit_auditevent")


def test_the_application_role_can_read_the_audit_trail(app_connection) -> None:  # noqa: ANN001
    """Reading is permitted and is itself an auditable action. The control is on
 modification, not on access."""
    with app_connection.cursor() as cursor:
        cursor.execute("SELECT count(*) FROM audit_auditevent")
        assert cursor.fetchone()[0] >= 0


def test_the_grants_on_the_audit_table_are_exactly_select_and_insert() -> None:
    """Asserted as an equality rather than as two absences. A later migration that granted `UPDATE`
 back - or a platform whose defaults differ - would pass a test that only checked `DELETE` was
 missing."""
    assert privileges_on(AUDIT_TABLE, settings.DB_APP_ROLE) == {"SELECT", "INSERT"}


@pytest.mark.parametrize(
    "table",
    [
        "cases_casemembership",
        "cases_runcorpus",
        "config_setting",
        "datasets_corpus",
        "datasets_mount",
        "review_resultorder",
        "review_tag",
        "search_clipembedding",
        "search_encoder",
        "tasks_taskrun",
    ],
)
def test_the_application_role_retains_full_access_to_ordinary_tables(table: str) -> None:
    """The revocation is narrow on purpose. A blanket read-only application would not be a safer
 system, it would be a non-functional one, and the temptation would then be to run the API as the
 owner.

 "Ordinary" here means every table that is not in the evidential list of - a membership
 that can be removed, a tag that can be retired, a vector that can be recomputed. The evidential
 tables are narrowed further by `datasets.0003_evidential_revocations` and asserted in
 `test_evidential_immutability.py`; listing one here would assert the opposite of the policy.
 """
    assert {"SELECT", "INSERT", "UPDATE", "DELETE"} <= privileges_on(table, settings.DB_APP_ROLE)


def test_the_application_role_cannot_change_the_schema(app_connection) -> None:  # noqa: ANN001
    """Schema changes belong to `migrate`, running as the owner. An application that can add
 a column can also drop a constraint, and every guarantee in rests on the
 constraints staying where the migrations put them."""
    with (
        pytest.raises(ProgrammingError, match="permission denied|must be owner"),
        app_connection.cursor() as cursor,
    ):
        cursor.execute("ALTER TABLE cases_case ADD COLUMN smuggled text")


def test_the_application_role_cannot_create_a_table(app_connection) -> None:  # noqa: ANN001
    with pytest.raises(ProgrammingError, match="permission denied"), app_connection.cursor() as c:
        c.execute("CREATE TABLE improvised (id integer)")


def test_the_application_role_can_use_the_sequences_it_needs(app_connection) -> None:  # noqa: ANN001
    """Granted separately from the tables, and the omission is silent until the first insert into a
 table with an auto-incrementing key - which is every table here."""
    with app_connection.cursor() as cursor:
        cursor.execute("SELECT last_value FROM cases_case_id_seq")
        assert cursor.fetchone is not None


def test_the_readonly_role_cannot_write() -> None:
    """The diagnostic role. It exists so that someone investigating a problem does not have to use
 the application's credentials, and it is only useful if it genuinely cannot change anything."""
    for table in ("cases_case", AUDIT_TABLE, "datasets_corpus"):
        privileges = privileges_on(table, settings.DB_READONLY_ROLE)
        assert privileges == {"SELECT"}, f"{table} grants {privileges} to the diagnostic role"


def test_later_tables_will_be_granted_without_another_migration() -> None:
    """The `ALTER DEFAULT PRIVILEGES` half of the migration, which is the half that stops this
 becoming a chore. Without it every future table would need its own grant, and the one that was
 forgotten would surface as a permission error in production rather than here."""
    with connection.cursor() as cursor:
        cursor.execute(
            """
 SELECT unnest(defaclacl)::text
 FROM pg_default_acl d
 JOIN pg_namespace n ON n.oid = d.defaclnamespace
 WHERE n.nspname = 'public' AND d.defaclobjtype = 'r'
 """
        )
        entries = [row[0] for row in cursor.fetchall()]

    app = settings.DB_APP_ROLE
    granted = [entry for entry in entries if entry.startswith(f"{app}=")]
    assert granted, f"no default table privileges for {app}: {entries}"
    # arwd is SELECT, UPDATE, INSERT, DELETE in PostgreSQL's ACL notation.
    assert all(letter in granted[0] for letter in "arwd")
