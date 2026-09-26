"""Local development. Never used for anything reachable from a network."""

from .base import *  # noqa: F403
from .base import env

DEBUG = True
SECRET_KEY = env("DJANGO_SECRET_KEY", "dev-only-not-a-secret")
ALLOWED_HOSTS = ["localhost", "127.0.0.1"]
# Browser origins that post to this API during local development.
CSRF_TRUSTED_ORIGINS = [
    "http://127.0.0.1:5173",
    "http://localhost:5173",
    "http://127.0.0.1:8000",
]
# Loopback only. Enrolment and /login/mfa remain in the tree for production.
MFA_CHALLENGE_AFTER_PASSWORD = False
LOCAL_FOLDER_INGEST = True
