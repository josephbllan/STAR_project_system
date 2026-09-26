"""The grants and the revocations: privilege separation made real.

Until this migration, `shoerag_app` could not read a single table of a freshly created database and
the audit trail was append-only by convention. Both facts follow from the same thing: the default
privileges in `infrastructure/compose/init/01-roles.sql` apply to the `public` schema *of the
database they were issued in*, and a test database created by the runner is a different database.

So the grants belong here, with the tables they apply to, and in the migration history where they can
be reviewed alongside them. Three decisions are worth stating.

**The role names come from settings, not from this file.** A managed platform issues its own names. A
migration with `shoerag_app` written into its SQL would either fail there or - far worse - succeed
against a role that does not exist, silently leaving the audit trail writable.

**A missing role is a hard failure, not a warning.** If the role cannot be found, this migration
raises. The alternative is a system that appears to have privilege separation and does not, which is
the failure this whole mechanism exists to prevent, arrived at by a different route.

**The revocation is `UPDATE` and `DELETE`, not `INSERT`.** The application must still be able to
record events; what it must not be able to do is rewrite or remove one. The owner retains both, which
is the honest position: a role that can run migrations can do anything, and the separation is between
the application and the schema owner rather than between the owner and the truth.

`REVOKE TRUNCATE` is included explicitly. `TRUNCATE` is a separate privilege from `DELETE` in
PostgreSQL, and a role with it can empty the audit trail in one statement without holding `DELETE` at
all - which is precisely the kind of gap that makes a control theatre.
"""

from __future__ import annotations

from django.conf import settings
from django.db import migrations

#: The audit trail. Named here rather than derived from the model, because a migration must describe
#: the schema as it was at this point and a later rename must not silently retarget these statements.
AUDIT_TABLE = "audit_auditevent"


def role_or_fail(connection, role: str) -> None:
    with connection.cursor() as cursor:
        cursor.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", [role])
        if cursor.fetchone is None:
            raise RuntimeError(
                f"The database role {role!r} does not exist, so the grants and revocations of "
                "cannot be applied. Create the role before migrating, or set DB_APP_ROLE "
                "and DB_READONLY_ROLE to the names this platform issues. This is refused rather "
                "than skipped because a system that appears to have privilege separation and does "
                "not is worse than one that admits it has none."
            )


def apply_privileges(apps, schema_editor) -> None:  # noqa: ARG001
    app_role = settings.DB_APP_ROLE
    readonly_role = settings.DB_READONLY_ROLE
    connection = schema_editor.connection

    for role in (app_role, readonly_role):
        role_or_fail(connection, role)

    with connection.cursor() as cursor:
        # Identifiers cannot be passed as parameters, so they are quoted. The values come from
        # settings rather than from a request, and the role has just been confirmed to exist.
        app = connection.ops.quote_name(app_role)
        readonly = connection.ops.quote_name(readonly_role)

        cursor.execute(f"GRANT USAGE ON SCHEMA public TO {app}, {readonly}")

        # What exists now.
        cursor.execute(
            f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {app}"
        )
        cursor.execute(f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {app}")
        cursor.execute(f"GRANT SELECT ON ALL TABLES IN SCHEMA public TO {readonly}")

        # What later migrations will create. Without this, every subsequent table would need its own
        # grant and the one that was forgotten would surface as a permission error in production.
        cursor.execute(
            "ALTER DEFAULT PRIVILEGES IN SCHEMA public "
            f"GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {app}"
        )
        cursor.execute(
            f"ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT USAGE, SELECT ON SEQUENCES TO {app}"
        )
        cursor.execute(
            f"ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO {readonly}"
        )

        # And the point of the whole migration.
        cursor.execute(f"REVOKE UPDATE, DELETE, TRUNCATE ON {AUDIT_TABLE} FROM {app}")


def remove_privileges(apps, schema_editor) -> None:  # noqa: ARG001
    """Reversible, and deliberately asymmetric: this revokes the grants rather than restoring the
 ability to rewrite the audit trail.

 A reversal that handed `UPDATE` on `audit_auditevent` back would make `migrate audit 0001` an
 instruction for erasing history. Reversing this migration leaves the application role with no
 access at all, which is inconvenient and safe.
 """
    app_role = settings.DB_APP_ROLE
    readonly_role = settings.DB_READONLY_ROLE
    connection = schema_editor.connection

    with connection.cursor() as cursor:
        app = connection.ops.quote_name(app_role)
        readonly = connection.ops.quote_name(readonly_role)

        cursor.execute(f"ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON TABLES FROM {app}")
        cursor.execute(
            f"ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON SEQUENCES FROM {app}"
        )
        cursor.execute(
            f"ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON TABLES FROM {readonly}"
        )
        cursor.execute(f"REVOKE ALL ON ALL TABLES IN SCHEMA public FROM {app}, {readonly}")
        cursor.execute(f"REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM {app}")


class Migration(migrations.Migration):
    """Depends on every app whose tables must be granted.

 `GRANT... ON ALL TABLES` is evaluated at the moment it runs and covers nothing created
 afterwards, so this migration has to be last. The `ALTER DEFAULT PRIVILEGES` above is what makes
 the ordering non-critical from here on, but it cannot retroactively cover what already exists -
 which is why the dependency list is explicit rather than trusting the app registry.
 """

    dependencies = [
        ("accounts", "0001_initial"),
        ("audit", "0001_initial"),
        ("cases", "0001_initial"),
        ("common", "0001_extensions"),
        ("config", "0001_initial"),
        ("datasets", "0002_add_evidencefile_case"),
        ("reporting", "0001_initial"),
        ("review", "0002_note_uq_review_note_public_id"),
        ("search", "0001_initial"),
        ("tasks", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(apply_privileges, remove_privileges),
    ]
