"""The recorder: `apps.audit.recorder`, the only write path to the trail.

Three groups of assertion. What the recorder puts in a row, which is where and either
hold or do not. What it refuses, which is the narrow mechanical half of And that it is the
only thing writing to the table at all - asserted by reading the source tree, because a second write
path added six months from now would otherwise be discovered by its absence from the trail.
"""

from __future__ import annotations

import re
import uuid
from pathlib import Path

import pytest
from django.test import RequestFactory
from django_guid import clear_guid, set_guid

from apps.audit import recorder
from apps.audit.models import AuditAction, AuditEvent, AuditOutcome
from apps.datasets.models import Corpus
from tests.factories.accounts import InvestigatorFactory
from tests.factories.datasets import CorpusFactory

pytestmark = [pytest.mark.integration, pytest.mark.django_db]

BACKEND_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def _no_inherited_correlation_id():
    """The identifier is held in a context variable, so a value set by one test would otherwise be
 visible to the next, and the tests asserting a generated one would pass for the wrong reason."""
    clear_guid()
    yield
    clear_guid()


# ----------------------------------------------------------------------------------------
# What a row contains.
# ----------------------------------------------------------------------------------------


def test_recording_writes_one_row() -> None:
    event = recorder.record(AuditAction.CASE_CREATED, actor=InvestigatorFactory())
    assert AuditEvent.objects.count() == 1
    assert event.action == AuditAction.CASE_CREATED
    assert event.outcome == AuditOutcome.SUCCEEDED


def test_the_default_outcome_is_success() -> None:
    """Chosen deliberately. Most call sites record something that worked, and a required argument
 that is nearly always the same value is an argument that gets passed wrongly."""
    assert recorder.record(AuditAction.CASE_CREATED).outcome == AuditOutcome.SUCCEEDED


def test_the_actor_is_snapshotted_as_well_as_referenced() -> None:
    """The key preserves attribution and refuses to let the account be deleted; the snapshot
 preserves what was true at the time."""
    actor = InvestigatorFactory(username="j.smith")
    event = recorder.record(AuditAction.CASE_CREATED, actor=actor)

    assert event.actor_id == actor.pk
    assert event.actor_username == "j.smith"
    assert event.actor_role == actor.role


def test_a_rename_after_the_event_does_not_restate_it() -> None:
    actor = InvestigatorFactory(username="j.smith")
    event = recorder.record(AuditAction.CASE_CREATED, actor=actor)

    actor.username = "j.smith-2"
    actor.save(update_fields=["username"])
    event.refresh_from_db()

    assert event.actor_username == "j.smith"


def test_the_actor_is_taken_from_the_request_when_not_given() -> None:
    actor = InvestigatorFactory()
    request = RequestFactory.post("/api/cases/")
    request.user = actor

    event = recorder.record(AuditAction.CASE_CREATED, request=request)
    assert event.actor_id == actor.pk


def test_an_explicit_actor_wins_over_the_request() -> None:
    """The case this matters for is administrative: an action performed *on* one account *by*
 another. The actor is who did it, never who it was done to."""
    administrator = InvestigatorFactory(username="admin")
    subject = InvestigatorFactory(username="subject")
    request = RequestFactory.post("/api/users/")
    request.user = subject

    event = recorder.record(AuditAction.ROLE_CHANGED, actor=administrator, request=request)
    assert event.actor_username == "admin"


def test_an_unauthenticated_request_records_an_anonymous_actor() -> None:
    """`ck_audit_auditevent_actor_identified` requires one of the two forms of attribution. A
 request refused before an identity was established has no key, and `anonymous` is the honest
 snapshot."""
    request = RequestFactory.get("/api/cases/")
    event = recorder.record_denial(AuditAction.AUTHZ_DENIED, request=request)

    assert event.actor_id is None
    assert event.actor_username == recorder.ANONYMOUS_ACTOR
    assert event.outcome == AuditOutcome.DENIED


def test_work_with_no_request_and_no_actor_is_attributed_to_the_system() -> None:
    """A scheduled integrity sweep, a data migration, a worker acting on its own timetable. Not
 `anonymous`, because those two are different things and an investigation needs to tell them
 apart."""
    event = recorder.record(AuditAction.INTEGRITY_VERIFIED)
    assert event.actor_id is None
    assert event.actor_username == recorder.SYSTEM_ACTOR


def test_the_request_method_and_path_are_recorded() -> None:
    request = RequestFactory.delete("/api/cases/17/members/3/")
    event = recorder.record(AuditAction.MEMBERSHIP_REVOKED, request=request)

    assert event.request_method == "DELETE"
    assert event.request_path == "/api/cases/17/members/3/"


def test_an_absurdly_long_path_is_truncated_rather_than_refused() -> None:
    """Losing the tail of a path is preferable to losing the event. A caller producing a path this
 long is doing something odd, and the record of it is the useful part."""
    request = RequestFactory.get("/api/cases/" + "a" * 500)
    event = recorder.record(AuditAction.RESULTS_VIEWED, request=request)
    assert len(event.request_path) == 300


def test_the_source_address_comes_from_the_peer_and_not_from_a_header() -> None:
    """A forwarded header is client-controlled unless a proxy that overwrites it is known to be in
 front. Trusting it would let a caller write any address into the trail, and the address is the
 field an investigation is most likely to act on."""
    request = RequestFactory.get("/api/cases/", headers={"x-forwarded-for": "203.0.113.9"})
    request.META["REMOTE_ADDR"] = "10.0.0.4"

    assert recorder.record(AuditAction.RESULTS_VIEWED, request=request).source_ip == "10.0.0.4"


def test_an_event_without_a_request_has_no_source_address() -> None:
    assert recorder.record(AuditAction.INTEGRITY_VERIFIED).source_ip is None


# ----------------------------------------------------------------------------------------
# The target.
# ----------------------------------------------------------------------------------------


def test_a_model_target_is_described_by_type_key_and_public_identifier() -> None:
    """Generic rather than a foreign key, because the trail records events about things that
 may not be rows at all. Where the target *is* a row, its numeric key goes in too, purely so the
 trail can be joined."""
    corpus = CorpusFactory()
    event = recorder.record(AuditAction.CORPUS_CREATED, target=corpus)

    assert event.target_type == "corpus"
    assert event.target_id == corpus.pk


def test_a_target_without_a_public_identifier_records_none() -> None:
    """`Corpus` has no public identifier - it is referred to by its code. The column stays null
 rather than being filled with something invented."""
    event = recorder.record(AuditAction.CORPUS_CREATED, target=CorpusFactory())
    assert event.target_public_id is None
    assert not hasattr(Corpus, "public_id")


def test_a_target_that_is_not_a_row_is_named_directly() -> None:
    """A session, a signed URL, a login attempt. The type is a string because there is nothing to
 take it from."""
    event = recorder.record(AuditAction.EVIDENCE_URL_ISSUED, target_type="signed_url")
    assert event.target_type == "signed_url"
    assert event.target_id is None


def test_an_explicit_type_overrides_the_one_derived_from_the_instance() -> None:
    corpus = CorpusFactory()
    event = recorder.record(AuditAction.CORPUS_CREATED, target=corpus, target_type="corpus_import")

    assert event.target_type == "corpus_import"
    assert event.target_id == corpus.pk


def test_an_event_may_have_no_target() -> None:
    """A sign-in is about the actor. A target column filled with the actor again would be noise."""
    event = recorder.record(AuditAction.LOGIN_SUCCEEDED, actor=InvestigatorFactory())
    assert event.target_type == ""


def test_the_target_label_is_recorded_when_given() -> None:
    corpus = CorpusFactory(code="SharedRef")
    event = recorder.record(AuditAction.CORPUS_CREATED, target=corpus, target_label="SharedRef")
    assert event.target_label == "SharedRef"


# ----------------------------------------------------------------------------------------
# The correlation identifier.
# ----------------------------------------------------------------------------------------


def test_the_correlation_identifier_is_taken_from_the_request() -> None:
    """and the operational point of the whole field: one user-reported failure resolves to
 the request, the log lines it produced, and the asynchronous work it dispatched."""
    supplied = uuid.uuid4()
    set_guid(str(supplied))

    assert recorder.record(AuditAction.CASE_CREATED).correlation_id == supplied


def test_events_in_one_request_share_an_identifier() -> None:
    set_guid(str(uuid.uuid4()))
    first = recorder.record(AuditAction.CASE_CREATED)
    second = recorder.record(AuditAction.CASE_METADATA_CHANGED)

    assert first.correlation_id == second.correlation_id


def test_an_identifier_is_generated_outside_a_request() -> None:
    """A Celery task, a management command, a scheduled sweep. A nullable column would be null in
 precisely the cases that are hardest to investigate."""
    event = recorder.record(AuditAction.INTEGRITY_VERIFIED)
    assert isinstance(event.correlation_id, uuid.UUID)


def test_two_units_of_work_outside_a_request_do_not_share_an_identifier() -> None:
    first = recorder.record(AuditAction.INTEGRITY_VERIFIED)
    second = recorder.record(AuditAction.INTEGRITY_VERIFIED)
    assert first.correlation_id != second.correlation_id


def test_a_malformed_identifier_does_not_prevent_the_event() -> None:
    """`VALIDATE_GUID` makes this unreachable through a request. It is handled anyway, because the
 alternative is an audit write failing on a malformed header - turning a logging concern into an
 outage."""
    set_guid("not-a-uuid")
    assert isinstance(recorder.record(AuditAction.CASE_CREATED).correlation_id, uuid.UUID)


# ----------------------------------------------------------------------------------------
# What the recorder refuses.
# ----------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "key", ["password", "token", "api_key", "secret", "credential", "connection_string"]
)
def test_a_credential_in_the_detail_is_refused(key: str) -> None:
    """The narrow, mechanical half of Refused rather than redacted: a call site passing one
 is a defect, and dropping it silently would leave the author believing it had been recorded."""
    with pytest.raises(recorder.AuditDetailError):
        recorder.record(AuditAction.PASSWORD_CHANGED, detail={key: "hunter2"})


def test_nothing_is_written_when_the_detail_is_refused() -> None:
    """The refusal happens before the insert, so a rejected call leaves no partial row. Worth
 asserting because the opposite - an event recorded with the offending key stripped - is the
 behaviour a reader might assume."""
    with pytest.raises(recorder.AuditDetailError):
        recorder.record(AuditAction.PASSWORD_CHANGED, detail={"password": "hunter2"})
    assert not AuditEvent.objects.exists()


def test_a_field_name_in_the_detail_is_the_intended_use() -> None:
    """Names of what changed, parameters of what was filtered. Never the value."""
    event = recorder.record(
        AuditAction.CASE_METADATA_CHANGED, detail={"changed": ["description", "reference"]}
    )
    assert event.detail == {"changed": ["description", "reference"]}


def test_an_action_outside_the_taxonomy_is_refused_by_the_database() -> None:
    """The recorder does not validate the action itself. It does not need to: the check constraint
 does, and duplicating the list in Python would create two places for it to drift."""
    from django.db import IntegrityError, transaction

    with pytest.raises(IntegrityError), transaction.atomic():
        recorder.record("evidence.looked_at")


def test_a_failure_to_record_propagates() -> None:
    """The decision that matters most in this module. The alternative - log a warning and carry on -
 produces a system that works perfectly while recording nothing, and the gap is found by whoever
 later needs the trail as evidence."""
    from django.db import IntegrityError, transaction

    with pytest.raises(IntegrityError), transaction.atomic():
        recorder.record(AuditAction.CASE_CREATED, outcome="partially")


# ----------------------------------------------------------------------------------------
# Convenience paths.
# ----------------------------------------------------------------------------------------


def test_a_denial_records_the_denied_outcome() -> None:
    """A separate function rather than an argument, because "denied" is the outcome a call site is
 most likely to forget to pass - and a trail of successes is a usage log."""
    corpus = CorpusFactory()
    event = recorder.record_denial(AuditAction.AUTHZ_DENIED, target=corpus)

    assert event.outcome == AuditOutcome.DENIED
    assert event.target_id == corpus.pk


def test_a_failed_sign_in_is_attributed_to_the_name_that_was_tried() -> None:
    """Recorded even though no account may exist under it. A series of attempts against one name is
 the pattern worth seeing, and it is invisible if every failure is recorded as `anonymous`."""
    request = RequestFactory.post("/api/auth/login/")
    event = recorder.record_failed_authentication(
        request=request, attempted_username="j.smith", reason="bad-credentials"
    )

    assert event.action == AuditAction.LOGIN_FAILED
    assert event.outcome == AuditOutcome.FAILED
    assert event.actor_id is None
    assert event.actor_username == "j.smith"


def test_a_failed_sign_in_records_no_credential() -> None:
    """`reason` is a short code and not a message, so that no part of what was typed reaches the
 trail - and nothing indicates which half was wrong, which is itself information an attacker
 wants."""
    event = recorder.record_failed_authentication(
        attempted_username="j.smith", reason="bad-credentials"
    )
    assert event.detail == {"reason": "bad-credentials"}


def test_an_attempted_username_is_truncated_to_the_column() -> None:
    """A login form is a place a caller can put five hundred characters. The event still gets
 written."""
    event = recorder.record_failed_authentication(attempted_username="x" * 400)
    assert len(event.actor_username) == 150


# ----------------------------------------------------------------------------------------
# The recorder is the only write path.
# ----------------------------------------------------------------------------------------

#: A write, by any of the three routes there are: the manager, the constructor, or raw SQL.
#:
#: `class AuditEvent(` is excluded by the lookbehind, and the table name is matched only when a
#: statement verb precedes it - a first draft of this pattern matched the bare name and flagged six
#: files that merely mention it in a comment, a constraint name or a `pg_constraint` query.
WRITE_PATTERN = re.compile(
    r"AuditEvent\.objects\.(create|bulk_create|update|get_or_create)"
    r"|(?<!class )AuditEvent\("
    r"|(?i:insert into|update|delete from)\s+audit_auditevent"
)

#: Where writing is legitimate. The recorder, because that is its job. The factory, because tests
#: must be able to arrange a trail. The privilege tests, which insert through a raw connection in
#: order to prove the role may. The migrations, which create the table and set its grants.
PERMITTED = {
    Path("apps/audit/recorder.py"),
    Path("apps/audit/migrations/0001_initial.py"),
    Path("apps/audit/migrations/0002_privileges.py"),
    Path("tests/factories/audit.py"),
    Path("tests/security/test_database_privileges.py"),
    Path("tests/integration/test_audit_recorder.py"),
}


def source_files() -> list[Path]:
    return [
        path
        for path in BACKEND_ROOT.rglob("*.py")
        if ".venv" not in path.parts and ".mypy_cache" not in path.parts
    ]


def test_there_is_something_to_scan() -> None:
    """Without this the scan below would pass on an empty list, and would look like a control while
 providing none."""
    assert len(source_files) > 50


def test_nothing_outside_the_recorder_writes_to_the_audit_table() -> None:
    """The structural half of "the recorder is the only write path". A second path added six months
 from now would otherwise be discovered by the absence of its events from the trail - during an
 incident, by the person who needed them.
 """
    offenders = sorted(
        str(path.relative_to(BACKEND_ROOT))
        for path in source_files
        if path.relative_to(BACKEND_ROOT) not in PERMITTED
        and WRITE_PATTERN.search(path.read_text(encoding="utf-8"))
    )
    assert not offenders, (
        "these files write to the audit table without going through apps.audit.recorder, so their "
        f"events may lack an actor snapshot, a correlation identifier, or both: {offenders}"
    )
