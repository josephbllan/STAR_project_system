"""Shared fixtures, and nothing else.

Environment loading happens in `config/settings/test.py` before `base` is imported.
`pytest-django` reads `DATABASES` before this file runs, so a load placed here would be inert.
Users are created through the manager so role constraints are exercised by the fixtures.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

from apps.accounts.models import EXPORT_CAPABLE_ROLES, Role
from apps.common import storage

User = get_user_model()

#: The shared password. Never a per-fixture value, because a test that needs to sign in
#: should not have to discover which one it was given.
PASSWORD = "correct-horse-battery-staple"  # noqa: S105


def _make_user(username: str, role: str):
    """One construction path for every role.

 `mfa_enforced` follows from the role rather than from the caller: the three roles that
 permit export cannot exist without it, and a fixture passing `False` would fail on a check
 constraint rather than produce a user.
 """
    return User.objects.create_user(
        username=username,
        password=PASSWORD,
        role=role,
        mfa_enforced=role in EXPORT_CAPABLE_ROLES,
    )


@pytest.fixture
def administrator(db):
    return _make_user("test-administrator", Role.ADMINISTRATOR)


@pytest.fixture
def investigator(db):
    return _make_user("test-investigator", Role.INVESTIGATOR)


@pytest.fixture
def analyst(db):
    return _make_user("test-analyst", Role.ANALYST)


@pytest.fixture
def reviewer(db):
    return _make_user("test-reviewer", Role.REVIEWER)


@pytest.fixture
def auditor(db):
    return _make_user("test-auditor", Role.AUDITOR)


@pytest.fixture(params=[role.value for role in Role])
def all_roles(request, db):
    """Yields one user per role in turn.

 Parametrised rather than five copied test functions, so that adding a role obliges whoever
 adds it to state the expected outcome for every protected endpoint instead of omitting the
 combinations they did not think about ( 5.4).
 """
    role = request.param
    return _make_user(f"test-{role}", role)


@pytest.fixture
def api_client() -> APIClient:
    """Unauthenticated. The default permission class is `IsAuthenticated`, so this client is
 what asserts that the default actually holds."""
    return APIClient()


@pytest.fixture
def client_as() -> Callable[[object], APIClient]:
    """A factory taking a user and returning a session-authenticated client.

 A factory rather than five fixtures because the parametrised permission tests select the
 role at run time, and `force_login` rather than a posted credential because these tests are
 about authorisation; the authentication flow has its own tests.
 """

    def _login(user: object) -> APIClient:
        client = APIClient()
        client.force_login(user)
        return client

    return _login


@pytest.fixture
def tmp_storage(tmp_path: Path, settings) -> Iterator[Path]:
    """Points `apps.common.storage` at a directory belonging to this test alone.

 The module caches its backend on first use, so the cache is cleared on both sides of the
 test: without the second reset a later test inherits this directory and the isolation is
 silently one-way.
 """
    root = tmp_path / "storage"
    root.mkdir()
    settings.STORAGE_ROOT = str(root)
    storage.reset_backend
    yield root
    storage.reset_backend


@pytest.fixture
def case_team(db):
    """A case with an owner, a reader, a contributor and an outsider (`tests/scenarios.py`).

 A fixture for the common single-case test; the builder is still there for a test that needs two
 cases and cannot ask for one fixture twice.
 """
    from tests import scenarios

    return scenarios.case_team


@pytest.fixture
def searchable_corpus(db):
    """Five evidence files with vectors from both encoders.

 Five rather than a larger number because this fixture serves correctness tests, where the cost
 of every extra row is paid by every test that touches it. The volume figures of belong to
 the `ml`-marked suite, which seeds its own.
 """
    from tests import scenarios

    return scenarios.searchable_corpus


@pytest.fixture
def completed_run(db):
    """A finished run with five ranked results over its own corpus."""
    from tests import scenarios

    return scenarios.completed_run


@pytest.fixture(scope="session", autouse=True)
def _release_the_mirror_connection(django_db_setup) -> Iterator[None]:  # noqa: ANN001
    """Closes the `app` connection before the runner drops the test database.

 Django opens the mirror alias for any test that declares it, and leaves it open. The runner then
 cannot drop the database - `is being accessed by other users` - and the failure appears as a
 teardown warning at the end of an otherwise green run, which is a thing nobody investigates.

 Depends on `django_db_setup` so that this fixture is set up after it and therefore finalised
 before it. Ordering is the whole content of this fixture.
 """
    yield
    from django.db import connections

    if "app" in connections.databases:
        connections["app"].close()


@pytest.fixture
def app_connection(db) -> Iterator[object]:
    """A second connection authenticated as `shoerag_app`, the role the application uses.

 Every other test connects as the owner, because the test runner must create and drop the test
 database, and a test connecting as the owner can never observe a privilege refusal - it would be
 measuring the wrong role's privileges. This fixture is the only way an append-only assertion
 means anything.

 Two properties of it are easy to get wrong and worth stating.

 The alias is a *mirror*, so Django does not wrap it in the per-test transaction it wraps
 `default` in. Anything written through this connection would therefore be committed and outlive
 the test. The rollback below is what prevents that, and it is not optional.

 For the same reason this connection cannot see rows the test arranged through `default`: those
 are in an uncommitted transaction on another connection. A privilege test asserts what the role
 may *do*, not what it can see, and any row it needs it inserts itself.

 Tests using this fixture must declare both aliases:
 `@pytest.mark.django_db(databases=["default", "app"])`.
 """
    from django.db import connections, transaction

    connection = connections["app"]
    with transaction.atomic(using="app"):
        yield connection
        transaction.set_rollback(True, using="app")
    connection.close()
