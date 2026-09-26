"""Test settings. PostgreSQL, never SQLite.

The default connection is the *owner* here because the test runner must create and drop the test
database. A test connecting as the owner can never observe a privilege refusal, so it would be
measuring the wrong role's privileges - which is why the `app` alias below exists.

`app` is declared as a mirror. That is not a description of replication; it is how Django is told
"this alias points at another alias's test database, do not create a second one and do not migrate
it". Without it the runner would try to create `test_shoerag` twice, and the application role has no
privilege to create a database at all.
"""

from config.dotenv import load_local_env

# Must precede the import of `base`, which reads the environment at import time. Doing this
# from a conftest is not sufficient: pytest-django touches `settings.DATABASES` inside its
# own `pytest_load_initial_conftests` hook, which runs before any conftest is imported.
load_local_env()

import tempfile  # noqa: E402

import dj_database_url  # noqa: E402

from .base import *  # noqa: E402, F403
from .base import env  # noqa: E402

DEBUG = False
SECRET_KEY = "test-only-not-a-secret"  # noqa: S105
ALLOWED_HOSTS = ["testserver"]

DATABASES = {
    "default": dj_database_url.parse(env("MIGRATION_DATABASE_URL", required=True), conn_max_age=0),
    "app": dj_database_url.parse(env("DATABASE_URL", required=True), conn_max_age=0),
}

#: Points the application role at the test database the owner created, rather than at `shoerag`.
#: A privilege test that ran against the development database would be asserting against whatever
#: state that database happened to be in, and would write to it.
DATABASES["app"]["TEST"] = {"MIRROR": "default"}

PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
AXES_ENABLED = False
LOCAL_FOLDER_INGEST = True

# A directory per run, so no test can observe a file another test wrote and no absolute path
# is written down. `tmp_storage` in `conftest.py` narrows this to one directory per test.
STORAGE_ROOT = tempfile.mkdtemp(prefix="shoerag-test-storage-")
