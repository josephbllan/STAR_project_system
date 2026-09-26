"""Extensions are created by migration and by the owner role, never by hand.

No dependency on `accounts` is declared. `accounts_user` needs no extension:
`gen_random_uuid` has been a core PostgreSQL function since version 13, so `pgcrypto`
is not a precondition for the user table. Every later migration that declares a `vector`
column depends on this one explicitly, which is what actually fixes the ordering.
"""

from django.contrib.postgres.operations import CreateExtension
from django.db import migrations


class Migration(migrations.Migration):
    initial = True

    dependencies: list[tuple[str, str]] = []

    operations = [
        CreateExtension("vector"),
        CreateExtension("pgcrypto"),
    ]
