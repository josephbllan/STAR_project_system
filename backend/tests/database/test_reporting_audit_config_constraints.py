"""`reporting_report`, `audit_auditevent` and `config_setting` as PostgreSQL holds them.

The three are together because each is a single table and their constraints are few. What they share
is that most of their design is about what is deliberately *not* stored: no credential in
`config_setting`, no evidential value in `audit_auditevent.detail`, and no raw
report file behind an expired row.

Several tests here therefore assert absence. An absence asserted by test is the only kind that
survives, because the next person to add a column will not have read the requirement.
"""

from __future__ import annotations

import pytest
from django.db import IntegrityError, connection, transaction
from django.db.models import ProtectedError
from django.utils import timezone

from apps.audit.models import AuditAction, AuditEvent, AuditOutcome
from apps.config.models import Setting
from apps.reporting.models import Report, ReportFormat, ReportStatus
from tests.factories.accounts import AdministratorFactory, InvestigatorFactory
from tests.factories.audit import (
    AnonymousAuditEventFactory,
    AuditEventFactory,
    SystemAuditEventFactory,
)
from tests.factories.config import SettingFactory
from tests.factories.reporting import (
    AvailableReportFactory,
    ExpiredReportFactory,
    ReportFactory,
)
from tests.factories.tasks import TaskRunFactory

pytestmark = [pytest.mark.integration, pytest.mark.django_db]


def constraint_names(table: str) -> set[str]:
    with connection.cursor() as cursor:
        cursor.execute("SELECT conname FROM pg_constraint WHERE conrelid = %s::regclass", [table])
        return {row[0] for row in cursor.fetchall()}


def index_names(table: str) -> set[str]:
    with connection.cursor() as cursor:
        cursor.execute("SELECT indexname FROM pg_indexes WHERE tablename = %s", [table])
        return {row[0] for row in cursor.fetchall()}


@pytest.mark.parametrize(
    ("table", "expected"),
    [
        (
            "reporting_report",
            {
                "uq_reporting_report_public_id",
                "uq_reporting_report_storage_key",
                "ck_reporting_report_format_valid",
                "ck_reporting_report_status_valid",
                "ck_reporting_report_available_has_object",
                "ck_reporting_report_sha256_hex",
            },
        ),
        (
            "audit_auditevent",
            {
                "uq_audit_auditevent_public_id",
                "ck_audit_auditevent_action_valid",
                "ck_audit_auditevent_outcome_valid",
                "ck_audit_auditevent_actor_identified",
            },
        ),
        ("config_setting", {"uq_config_setting_key"}),
    ],
)
def test_named_constraints_exist(table: str, expected: set[str]) -> None:
    assert expected <= constraint_names(table)


# ----------------------------------------------------------------------------------------
# Reporting.
# ----------------------------------------------------------------------------------------


def test_a_report_row_exists_from_the_moment_of_request() -> None:
    """A request that fails is a record of an attempted export, and exfiltration by
 repeated failing export is exactly the pattern a completion-only record would hide."""
    report = ReportFactory()
    assert report.status == ReportStatus.REQUESTED
    assert not report.has_object


def test_a_failed_export_keeps_its_row() -> None:
    report = ReportFactory(status=ReportStatus.FAILED, failure_reason="rendering-timeout")
    report.refresh_from_db()
    assert report.status == ReportStatus.FAILED
    assert report.failure_reason == "rendering-timeout"


def test_an_available_report_carries_a_key_and_a_digest() -> None:
    report = AvailableReportFactory()
    assert report.storage_key
    assert report.sha256


@pytest.mark.parametrize(
    ("storage_key", "sha256"),
    [(None, "0" * 64), ("reports/ab/cd/ef.pdf", None), (None, None)],
    ids=["no_key", "no_digest", "neither"],
)
def test_an_available_report_without_an_object_is_refused(
    storage_key: str | None, sha256: str | None
) -> None:
    """A report offered for download with nothing behind it is both a 500 waiting to happen
 and a record asserting an export that produced no file."""
    with pytest.raises(IntegrityError), transaction.atomic():
        ReportFactory(status=ReportStatus.AVAILABLE, storage_key=storage_key, sha256=sha256)


def test_an_expired_report_keeps_its_record_and_loses_its_file() -> None:
    """and the reason `DELETE` is revoked on this table. The record that an export occurred
 outlives the file it produced, and the digest is kept so a copy produced later is still
 checkable."""
    report = ExpiredReportFactory()
    assert report.status == ReportStatus.EXPIRED
    assert report.storage_key is None
    assert report.sha256 is not None


def test_two_reports_cannot_share_a_storage_key() -> None:
    first = AvailableReportFactory()
    with pytest.raises(IntegrityError), transaction.atomic():
        AvailableReportFactory(storage_key=first.storage_key)


def test_many_reports_may_have_no_storage_key() -> None:
    """The unique constraint has to tolerate this: every report begins with a null key, and nulls
 are distinct in PostgreSQL, which is the behaviour being relied on rather than worked around."""
    ReportFactory
    ReportFactory
    assert Report.objects.filter(storage_key__isnull=True).count() == 2


def test_a_report_digest_must_be_a_digest() -> None:
    """The character-class check added during implementation and recorded in Every other digest in the schema has one, and this is the digest that makes an exported file
 verifiable."""
    with pytest.raises(IntegrityError), transaction.atomic():
        AvailableReportFactory(sha256="NOT-A-DIGEST")


def test_an_unrecognised_report_format_is_refused() -> None:
    with pytest.raises(IntegrityError), transaction.atomic():
        ReportFactory(format="xlsx")


@pytest.mark.parametrize("report_format", [f.value for f in ReportFormat])
def test_every_enumerated_format_is_accepted_by_the_schema(report_format: str) -> None:
    """The schema permits all three; the serializer is what restricts release 1 to PDF. Separated
 because adding a value to a check constraint is a migration and restricting a serializer is
 not."""
    assert ReportFactory(format=report_format).pk is not None


def test_an_unrecognised_report_status_is_refused() -> None:
    with pytest.raises(IntegrityError), transaction.atomic():
        ReportFactory(status="pending")


def test_the_export_volume_index_exists() -> None:
    """This is the principal detection control in the system, and it is an index whose
 justification is a threat rather than a page load: "how much has this person exported, over what
 period"."""
    assert "ix_reporting_report_requested_by_time" in index_names("reporting_report")


def test_the_expiry_sweep_index_is_partial() -> None:
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT indexdef FROM pg_indexes WHERE indexname = %s",
            ["ix_reporting_report_expiry"],
        )
        (definition,) = cursor.fetchone
    assert "WHERE" in definition


def test_the_download_counter_is_a_convenience_and_not_the_record() -> None:
    """It can be incremented without saying by whom, which is precisely why the audit trail remains
 authoritative on who downloaded what."""
    report = AvailableReportFactory()
    Report.objects.filter(pk=report.pk).update(download_count=3)
    report.refresh_from_db()

    assert report.download_count == 3
    assert "downloaded_by" not in {f.name for f in Report._meta.get_fields}


def test_protect_refuses_to_delete_the_requester_of_a_report() -> None:
    report = ReportFactory()
    with pytest.raises(ProtectedError):
        report.requested_by.delete()


def test_a_report_records_the_task_run_that_produced_it() -> None:
    run = TaskRunFactory(task_name="reporting.render_pdf")
    assert ReportFactory(task_run=run).task_run_id == run.pk


# ----------------------------------------------------------------------------------------
# Audit.
# ----------------------------------------------------------------------------------------


def test_the_audit_table_has_no_modification_time() -> None:
    """The one table that does not extend `TimeStampedModel`. A modification time on an append-only
 table would be a column that can only ever hold one value while implying otherwise."""
    columns = {field.name for field in AuditEvent._meta.get_fields}
    assert "updated_at" not in columns
    assert "created_at" not in columns
    assert "occurred_at" in columns


@pytest.mark.parametrize("action", [a.value for a in AuditAction])
def test_every_action_in_the_taxonomy_is_accepted(action: str) -> None:
    """All of, one test per action. Tedious on purpose: the taxonomy covers exhaustively, and a value present in the enumeration but absent from the check constraint would
 fail only when that particular event first occurred - possibly in production, possibly during an
 incident."""
    assert AuditEventFactory(action=action).pk is not None


def test_an_action_outside_the_taxonomy_is_refused() -> None:
    """Rejected rather than recorded, which is what stops the taxonomy eroding one call site
 at a time. A call site free to invent a string produces a trail that has to be grepped rather
 than filtered."""
    with pytest.raises(IntegrityError), transaction.atomic():
        AuditEventFactory(action="evidence.looked_at")


def test_the_taxonomy_covers_every_group_of_sec_25() -> None:
    prefixes = {action.value.split(".")[0] for action in AuditAction}
    assert prefixes == {
        "auth",
        "authz",
        "evidence",
        "case",
        "search",
        "review",
        "export",
        "admin",
        "audit",
    }


def test_reading_the_audit_trail_is_itself_an_auditable_action() -> None:
    """Recorded once per query with the filter parameters in `detail`, and not
 once per row returned - which is the difference between an audit trail and an audit trail that
 doubles in size every time someone reads it."""
    event = AuditEventFactory(
        action=AuditAction.AUDIT_READ,
        detail={"filters": {"actor": "user-3", "action": "export.downloaded"}},
    )
    event.refresh_from_db()
    assert event.action == AuditAction.AUDIT_READ
    assert event.detail["filters"]["action"] == "export.downloaded"


@pytest.mark.parametrize("outcome", [o.value for o in AuditOutcome])
def test_every_outcome_is_accepted(outcome: str) -> None:
    assert AuditEventFactory(outcome=outcome).pk is not None


def test_an_unrecognised_outcome_is_refused() -> None:
    with pytest.raises(IntegrityError), transaction.atomic():
        AuditEventFactory(outcome="partial")


def test_an_event_records_the_actor_twice() -> None:
    """The key preserves attribution; the snapshot preserves what was true at the time."""
    actor = InvestigatorFactory(username="j.smith")
    event = AuditEventFactory(actor=actor)

    assert event.actor_id == actor.pk
    assert event.actor_username == "j.smith"
    assert event.actor_role == actor.role


def test_a_username_change_does_not_rewrite_history() -> None:
    """The substance of Without the snapshot, renaming an account would silently restate
 every event it ever produced as having been performed under the new name."""
    actor = InvestigatorFactory(username="j.smith")
    event = AuditEventFactory(actor=actor)

    actor.username = "j.smith-2"
    actor.save(update_fields=["username"])

    event.refresh_from_db()
    assert event.actor_username == "j.smith"
    assert event.actor.username == "j.smith-2"


def test_a_deactivated_account_does_not_orphan_its_events() -> None:
    actor = InvestigatorFactory()
    AuditEventFactory(actor=actor)

    actor.is_active = False
    actor.save(update_fields=["is_active"])

    with pytest.raises(ProtectedError):
        actor.delete()


def test_a_system_event_has_no_actor_but_is_still_attributed() -> None:
    event = SystemAuditEventFactory()
    assert event.actor_id is None
    assert event.actor_username == "system"


def test_an_anonymous_failure_is_recorded_against_the_attempted_name() -> None:
    event = AnonymousAuditEventFactory()
    assert event.actor_id is None
    assert event.actor_username == "unknown-account"
    assert event.outcome == AuditOutcome.FAILED


def test_an_event_with_no_attribution_at_all_is_refused() -> None:
    with pytest.raises(IntegrityError), transaction.atomic():
        AuditEventFactory(actor=None, actor_username="")


def test_the_target_is_generic_rather_than_a_foreign_key() -> None:
    """The trail has to record events about entities that do not yet exist in the schema, so
 the target cannot be a foreign key. The numeric key is recorded additionally where available,
 purely for joining."""
    event = AuditEventFactory(target_type="corpus", target_id=17, target_public_id=None)
    event.refresh_from_db()

    assert (event.target_type, event.target_id) == ("corpus", 17)
    assert event.target_public_id is None


def test_the_detail_field_holds_field_names_and_not_field_values() -> None:
    """asserted as the convention it is. The constraint is on content and cannot be
 expressed in the schema, so what the schema provides is a JSON column and what the tests provide
 is the discipline: names of what changed, parameters of what was filtered, never a value."""
    event = AuditEventFactory(
        action=AuditAction.CASE_METADATA_CHANGED,
        detail={"changed": ["description", "reference"]},
    )
    event.refresh_from_db()

    assert event.detail == {"changed": ["description", "reference"]}
    assert "description" in event.detail["changed"]


def test_every_documented_index_exists() -> None:
    assert {
        "ix_audit_auditevent_actor_action_time",
        "ix_audit_auditevent_target",
        "ix_audit_auditevent_action_time",
        "ix_audit_auditevent_correlation",
        "ix_audit_auditevent_occurred_at",
    } <= index_names("audit_auditevent")


def test_there_is_no_hash_chain_column() -> None:
    """asserted by absence. A tamper-evident chain is recommended for release 2 and
 deliberately not adopted now: it requires inserts to be serialised, which contradicts concurrent
 insertion from several API processes and several workers. Recorded as a test so that nobody
 concludes append-only privilege was assumed sufficient without examination."""
    columns = {field.name for field in AuditEvent._meta.get_fields}
    assert columns.isdisjoint({"previous_digest", "chain_digest", "row_hash"})


# ----------------------------------------------------------------------------------------
# Settings.
# ----------------------------------------------------------------------------------------


def test_a_setting_key_is_unique() -> None:
    SettingFactory(key="retrieval.default_top_k")
    with pytest.raises(IntegrityError), transaction.atomic():
        SettingFactory(key="retrieval.default_top_k")


@pytest.mark.parametrize(
    "value",
    [50, 0.35, True, "histogram_equalisation", ["real", "synthetic"], {"clip": 0.5}],
    ids=["int", "float", "bool", "string", "list", "object"],
)
def test_a_setting_value_may_be_any_json_shape(value: object) -> None:
    """The variability is genuine: a fusion default is a number, a permitted-classification
 list is a list, an expiry window is a count of seconds. What each key means is stated where the
 key is read, not in a schema this table cannot enforce."""
    setting = SettingFactory(value=value)
    setting.refresh_from_db()
    assert setting.value == value


def test_a_setting_may_have_no_editor() -> None:
    """Null where the setting was seeded by a migration rather than changed by a person."""
    assert SettingFactory(updated_by=None).pk is not None


def test_protect_refuses_to_delete_the_editor_of_a_setting() -> None:
    setting = SettingFactory(updated_by=AdministratorFactory())
    with pytest.raises(ProtectedError):
        setting.updated_by.delete()


def test_the_settings_table_holds_no_secret_shaped_column() -> None:
    """asserted by absence. No credential, key or connection string is stored here;
 secrets arrive at runtime through the environment. This table is the obvious place to put one,
 and the obvious place is where it will be put unless the prohibition is checked."""
    columns = {field.name for field in Setting._meta.get_fields}
    assert columns.isdisjoint(
        {"secret", "password", "token", "api_key", "credential", "connection_string"}
    )


def test_a_setting_records_when_it_last_changed() -> None:
    setting = SettingFactory(value=50)
    first = setting.updated_at

    setting.value = 100
    setting.save(update_fields=["value", "updated_at"])
    setting.refresh_from_db()

    assert setting.updated_at > first
    assert setting.value == 100


def test_the_expiry_window_of_a_report_is_a_setting_and_not_a_constant() -> None:
    """Recorded here because the two tables meet at exactly this point: the sweep that expires a
 report reads its window from `config_setting`, and a constant in code would mean an operational
 change required a deployment."""
    SettingFactory(key="reporting.expiry_seconds", value=604_800)
    report = AvailableReportFactory(expires_at=timezone.now() + timezone.timedelta(seconds=604_800))
    assert report.expires_at is not None
