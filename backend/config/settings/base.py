"""Settings common to every environment. Reads configuration from the environment only."""

import os
from pathlib import Path

import dj_database_url

from apps.tasks.inventory import TASK_ROUTES, VISIBILITY_TIMEOUT_SECONDS

BASE_DIR = Path(__file__).resolve().parents[2]


def env(name: str, default: str | None = None, *, required: bool = False) -> str:
    value = os.environ.get(name, default)
    if required and not value:
        raise RuntimeError(f"Required environment variable {name} is not set")
    return value or ""


SECRET_KEY = env("DJANGO_SECRET_KEY")
DEBUG = False
ALLOWED_HOSTS: list[str] = []

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "drf_spectacular",
    "django_guid",
    "django_otp",
    "django_otp.plugins.otp_totp",
    "django_otp.plugins.otp_static",
    "axes",
    "apps.common",
    "apps.accounts",
    # Order here does not determine migration order - dependencies do - but it is kept in the
    # sequence of so the two documents read alike.
    "apps.tasks",
    "apps.datasets",
    "apps.search",
    "apps.indexing",
    "apps.cases",
    "apps.review",
    "apps.reporting",
    "apps.audit",
    "apps.config",
]

MIDDLEWARE = [
    # First, so that every log line and every error envelope produced downstream carries the
    # identifier. A correlation identifier assigned after something has already failed is of no
    # use to the person reading the log.
    "django_guid.middleware.guid_middleware",
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django_otp.middleware.OTPMiddleware",
    "axes.middleware.AxesMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

# Two connections. `default` is the application role and holds no schema privileges;
# `migration` is the owner and is used only by `migrate`.
# CONN_MAX_AGE is zero because the platform is scale-to-zero.
DATABASES = {
    "default": dj_database_url.parse(
        env("DATABASE_URL", required=True), conn_max_age=0, conn_health_checks=False
    ),
    "migration": dj_database_url.parse(
        env("MIGRATION_DATABASE_URL", required=True), conn_max_age=0, conn_health_checks=False
    ),
}

# The database role names, read by `apps.audit.migrations.0002_privileges` rather than written into
# its SQL. A managed platform issues its own role names, and a migration carrying a literal
# `shoerag_app` would either fail there or, worse, succeed against a role that does not exist and
# leave the audit trail writable.
DB_APP_ROLE = env("DB_APP_ROLE", default="shoerag_app")
DB_READONLY_ROLE = env("DB_READONLY_ROLE", default="shoerag_readonly")

AUTH_USER_MODEL = "accounts.User"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# models.E034 caps an index name at 30 characters. That limit is Oracle's historical identifier
# length, and this project is PostgreSQL in every environment including tests, where
# the limit is 63. Seventeen of the index names in exceed 30, and so do fifty-three
# of the constraint names - which Django permits, since it applies no such check to those. Keeping
# the index names short while the constraint names stay descriptive would break the single
# convention that exists to serve: a violation must name something locatable, because that
# name is what the 409 mapping keys on. A test asserts the real limit instead
# (tests/database/test_naming_convention.py).
SILENCED_SYSTEM_CHECKS = ["models.E034"]

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
        "OPTIONS": {"min_length": 12},
    },
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-gb"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

# Evidential files. The root arrives from the environment and appears in no source file, which
# is what keeps hosted object storage a settings change rather than a rewrite
# ( 2.4). `apps/common/storage.py` is the only reader, and it raises rather
# than guess a location if this is empty.
STORAGE_ROOT = env("SHOERAG_STORAGE_ROOT")

# The lifetime of a signed access grant. Short, measured in minutes, stated here rather than
# in code, and fixed into each token at the moment it is issued.
SIGNED_URL_TTL_SECONDS = int(env("SIGNED_URL_TTL_SECONDS", "600"))

# Restrictive by default; every relaxation is explicit at the view.
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework.authentication.SessionAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticated",
    ],
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "UNAUTHENTICATED_USER": "django.contrib.auth.models.AnonymousUser",
    # The sole producer of error responses. Without this, DRF emits four different failure
    # shapes from the same application.
    "EXCEPTION_HANDLER": "apps.common.exceptions.shoerag_exception_handler",
    # A default, so that an unpaginated collection cannot be created by omission. A viewset
    # over a growing table overrides it with the cursor class.
    "DEFAULT_PAGINATION_CLASS": "apps.common.pagination.StandardPagination",
}

# `type` in the error envelope is a stable identifier that monitoring aggregates on, so it is
# a URL under a name this project controls rather than a bare string.
PROBLEM_TYPE_BASE_URL = env("PROBLEM_TYPE_BASE_URL", "https://shoerag.example/errors")

DJANGO_GUID = {
    # A client-supplied identifier is accepted only if it is a valid UUID, and replaced
    # otherwise. An unvalidated header would let a caller write arbitrary text into every log
    # line the request produces.
    "VALIDATE_GUID": True,
    "RETURN_HEADER": True,
    "EXPOSE_HEADER": True,
    "GUID_HEADER_NAME": "Correlation-ID",
}

SPECTACULAR_SETTINGS = {
    "TITLE": "ShoeRAG Web API",
    "VERSION": "1.0.0",
    "SERVE_INCLUDE_SCHEMA": False,
    "SCHEMA_PATH_PREFIX": "/api/v1",
}

CELERY_BROKER_URL = env("CELERY_BROKER_URL", "redis://127.0.0.1:6379/0")
# No result backend. Unset, not set to a dummy: a dummy would re-enable chords.
CELERY_RESULT_BACKEND = None
CELERY_TASK_ACKS_LATE = True
CELERY_TASK_REJECT_ON_WORKER_LOST = True
CELERY_TASK_TRACK_STARTED = True
CELERY_TASK_ALWAYS_EAGER = False
CELERY_TASK_IGNORE_RESULT = True
CELERY_TASK_ROUTES = TASK_ROUTES
CELERY_TASK_DEFAULT_QUEUE = "default"
CELERY_TASK_QUEUES = {
    "default": {},
    "encoding": {},
    "reporting": {},
    "maintenance": {},
}
CELERY_WORKER_PREFETCH_MULTIPLIER = 1
CELERY_BROKER_TRANSPORT_OPTIONS = {"visibility_timeout": VISIBILITY_TIMEOUT_SECONDS}

# Image bounds applied before decode. Thirty thousand on a side matches the
# `ck_datasets_contentobject_dimensions_bounded` check.
MAX_IMAGE_PIXELS = 30_000 * 30_000
MAX_UPLOAD_BYTES = 50 * 1024 * 1024
PERMITTED_DATA_CLASSIFICATIONS = env("SHOERAG_PERMITTED_CLASSIFICATIONS", "synthetic").split(",")
TASK_RUN_RETENTION_DAYS = int(env("SHOERAG_TASK_RUN_RETENTION_DAYS", "30"))
SESSION_COOKIE_AGE = 8 * 60 * 60
AUTHENTICATION_BACKENDS = [
    "axes.backends.AxesStandaloneBackend",
    "django.contrib.auth.backends.ModelBackend",
]
AXES_ENABLED = True
AXES_FAILURE_LIMIT = 5
AXES_COOLOFF_TIME = 0.25
AXES_LOCKOUT_PARAMETERS = ["username", "ip_address"]
OTP_TOTP_ISSUER = "ShoeRAG"
# When False, a correct password completes the session even for export-capable roles.
# Local development turns this off; tests and production leave it on.
MFA_CHALLENGE_AFTER_PASSWORD = True
# Local composition may walk a folder on this machine (local folder ingest). Off in production.
LOCAL_FOLDER_INGEST = False
