"""`DELETE` revoked on the evidential tables, and `UPDATE` revoked on results.

The audit trail is not the only thing that must survive the application. Evidence, the runs that
searched it, the results those runs produced and the judgements recorded against them are all records
of what was done, and a system that can quietly remove one cannot be used to support a finding.

Three things about this migration are worth stating, because each was a decision rather than a
transcription of

**It lives in `datasets` but revokes across five apps.** The tables it protects belong to `datasets`,
`cases`, `review` and `reporting`, and splitting the statements between four migrations would mean
the policy could only be read by assembling it from four places. It is one policy; it is written
once. The dependency on `audit.0002_privileges` is what orders it after the grants it narrows.

**`UPDATE` is revoked on `cases_result` and nowhere else.** A result is what the system found at a
moment under a stated configuration. Code that needs to record a changed judgement about a
result writes a review record; it does not edit the finding. The practical consequence is that an ORM
`save` on a loaded `Result` fails as the application role, and that is the intent rather than an
inconvenience to be worked around.

**`TRUNCATE` is not revoked here, because it was never granted.** `audit.0002` grants
`SELECT, INSERT, UPDATE, DELETE` and nothing else, so the application role has no `TRUNCATE` on any
table. The revocation on the audit table is belt and braces on the single most important table; here
the absence is asserted by test instead, which is the cheaper way to keep a negative true.

Deletion remains available to the owner. That is the honest position: a role that can run migrations
can drop the table, and the separation is between the application and the schema owner rather than
between the owner and the truth. Retention and lawful disposal are operated deliberately, by the
owner, and recorded - not performed incidentally by a request.
"""

from __future__ import annotations

from django.conf import settings
from django.db import migrations

#: The evidential tables of Named as literals rather than derived from the app registry: a
#: migration describes the schema at a point in time, and a table added later must be considered
#: on its own merits rather than silently inheriting this policy.
EVIDENTIAL_TABLES = (
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
)

#: Immutable once written.
READ_ONLY_TABLES = ("cases_result",)


def revoke(apps, schema_editor) -> None:  # noqa: ARG001
    connection = schema_editor.connection
    app = connection.ops.quote_name(settings.DB_APP_ROLE)

    with connection.cursor() as cursor:
        for table in EVIDENTIAL_TABLES:
            cursor.execute(f"REVOKE DELETE ON {connection.ops.quote_name(table)} FROM {app}")
        for table in READ_ONLY_TABLES:
            cursor.execute(f"REVOKE UPDATE ON {connection.ops.quote_name(table)} FROM {app}")


def restore(apps, schema_editor) -> None:  # noqa: ARG001
    """Reversible, and reversing it really does hand deletion back.

 Unlike the audit revocation, which reverses by removing access rather than by restoring the
 ability to rewrite history, this one is a genuine inverse. The distinction is that the audit trail
 is append-only as a property of the system, whereas these tables are protected as a matter of
 policy - and a policy has to be changeable by the same mechanism that set it, or it will be
 changed by hand at a `psql` prompt and nobody will know.
 """
    connection = schema_editor.connection
    app = connection.ops.quote_name(settings.DB_APP_ROLE)

    with connection.cursor() as cursor:
        for table in EVIDENTIAL_TABLES:
            cursor.execute(f"GRANT DELETE ON {connection.ops.quote_name(table)} TO {app}")
        for table in READ_ONLY_TABLES:
            cursor.execute(f"GRANT UPDATE ON {connection.ops.quote_name(table)} TO {app}")


class Migration(migrations.Migration):
    dependencies = [
        # After the grants, which this narrows. Without the ordering, a `GRANT... ON ALL TABLES`
        # running afterwards would hand back everything revoked here, and the failure would be
        # invisible: every test would pass and the control would simply not be there.
        ("audit", "0002_privileges"),
        ("datasets", "0002_add_evidencefile_case"),
    ]

    operations = [
        migrations.RunPython(revoke, restore),
    ]
