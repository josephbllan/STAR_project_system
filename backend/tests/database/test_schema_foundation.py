"""The schema as PostgreSQL actually holds it.

Constraint existence is read back from `pg_constraint` rather than asserted against the model,
because a `CheckConstraint` declared in a model that never reached a migration is a comment
( 1). Each one is then exercised by attempting the violation, because a constraint
that exists and does not bite is the same as no constraint.
"""

import pytest
from django.contrib.auth import get_user_model
from django.db import IntegrityError, connection, transaction

User = get_user_model()

pytestmark = [pytest.mark.integration, pytest.mark.django_db]


def constraint_names(table: str) -> set[str]:
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT conname FROM pg_constraint WHERE conrelid = %s::regclass",
            [table],
        )
        return {row[0] for row in cursor.fetchall()}


def test_required_extensions_are_installed() -> None:
    """An image tag pins a major version of PostgreSQL and says nothing about the
 extension version shipped alongside it, so availability is queried and never inferred."""
    with connection.cursor() as cursor:
        cursor.execute("SELECT extname FROM pg_extension")
        installed = {row[0] for row in cursor.fetchall()}
    assert {"vector", "pgcrypto"} <= installed


def test_named_constraints_exist_on_user_table() -> None:
    """A constraint violation must name something locatable, because the name is what a
 structured 409 response is built from."""
    assert {
        "uq_accounts_user_username",
        "uq_accounts_user_public_id",
        "ck_accounts_user_role_valid",
        "ck_accounts_user_mfa_required_roles",
        "ck_accounts_user_mfa_confirmed_state",
    } <= constraint_names("accounts_user")


def test_username_uniqueness_is_a_named_constraint_not_an_implicit_index() -> None:
    """again, from the other direction. Declaring `unique=True` would produce a constraint
 named by Django, which the 409 mapping could not key on."""
    with pytest.raises(IntegrityError), transaction.atomic():
        User.objects.create_user(username="duplicate", password="correct-horse-battery")
        User.objects.create_user(username="duplicate", password="correct-horse-battery")


def test_public_id_is_assigned() -> None:
    """Opaque external identifier, present without the application having to set it."""
    user = User.objects.create_user(username="analyst", password="correct-horse-battery")
    assert user.public_id is not None


def test_public_id_has_a_database_default() -> None:
    """The column carries `gen_random_uuid` as well as a Python default, so a row inserted by
 a migration or by SQL is not left without an identifier."""
    with connection.cursor() as cursor:
        cursor.execute(
            """
 SELECT column_default FROM information_schema.columns
 WHERE table_name = 'accounts_user' AND column_name = 'public_id'
 """
        )
        default = cursor.fetchone()[0]
    assert default is not None and "gen_random_uuid" in default


def test_export_capable_role_cannot_exist_without_mfa() -> None:
    """enforced by the schema so that no administrative edit and no future serializer
 can bypass it."""
    with pytest.raises(IntegrityError), transaction.atomic():
        User.objects.create_user(
            username="investigator",
            password="correct-horse-battery",
            role="investigator",
            mfa_enforced=False,
        )


def test_analyst_may_exist_without_mfa() -> None:
    """The other half of the same constraint. Without this, a constraint that refused every row
 would also pass the test above."""
    user = User.objects.create_user(
        username="second-analyst", password="correct-horse-battery", role="analyst"
    )
    assert user.can_export is False


def test_mfa_confirmation_requires_enforcement() -> None:
    """`ck_accounts_user_mfa_confirmed_state`. A confirmation timestamp on a user for whom
 enforcement is off would read as enrolled while the check is not applied."""
    with pytest.raises(IntegrityError), transaction.atomic():
        User.objects.create_user(
            username="half-enrolled",
            password="correct-horse-battery",
            role="analyst",
            mfa_enforced=False,
            mfa_confirmed_at="2026-01-01T00:00:00+00:00",
        )


def test_role_must_be_one_of_the_five() -> None:
    """`ck_accounts_user_role_valid`. `choices` alone is a form-level restriction; this is the
 schema refusing."""
    with pytest.raises(IntegrityError), transaction.atomic():
        User.objects.create_user(
            username="impostor", password="correct-horse-battery", role="superuser"
        )
