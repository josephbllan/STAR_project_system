"""Configuration facts that everything else depends on being true.

These are here rather than in `database/` because they read settings and the connection's
declared vendor, touching no data. Each one fails loudly if a foundational assumption is
broken later, which is the only reason a test this small earns its place.
"""

import pytest
from django.conf import settings
from django.db import connection

pytestmark = pytest.mark.unit


def test_database_is_postgresql() -> None:
    """SQLite in tests would silently accept what PostgreSQL rejects, so a suite passing
 against it would be measuring a different system."""
    assert connection.vendor == "postgresql"


def test_user_model_is_substituted() -> None:
    """Substituting `AUTH_USER_MODEL` after `django.contrib.auth` has migrated is not
 recoverable without dropping the database."""
    assert settings.AUTH_USER_MODEL == "accounts.User"


def test_two_connections_are_configured() -> None:
    """The application role holds no schema privileges, so `migrate` needs its
 own alias. In the test settings both names exist even though both point at the owner."""
    assert "default" in settings.DATABASES


def test_permissions_are_restrictive_by_default() -> None:
    """Every relaxation is explicit at the view, which is only true if the default
 denies. A default of `AllowAny` would make every unannotated endpoint public."""
    assert settings.REST_FRAMEWORK["DEFAULT_PERMISSION_CLASSES"] == [
        "rest_framework.permissions.IsAuthenticated"
    ]


def test_no_result_backend_is_configured() -> None:
    """`tasks_taskrun` is the authoritative record of task state. A result backend
 would create a second store and the possibility of the two disagreeing."""
    assert not getattr(settings, "CELERY_RESULT_BACKEND", "")
