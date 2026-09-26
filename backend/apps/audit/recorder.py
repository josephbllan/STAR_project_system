"""The only write path to the audit trail.

Nothing else in this project constructs an `AuditEvent`. That is not a style preference: a trail
written from thirty call sites is a trail with thirty different ideas about what an actor is, and
the one call site that omits the correlation identifier is the one needed during an incident. A test
in `tests/integration/test_audit_recorder.py` reads the source tree and fails if anything outside
this module and the factories writes to the table.

Five decisions are embodied here and each is worth stating, because each could reasonably have gone
the other way.

**A failure to record propagates.** `record` catches nothing. If the event cannot be written, the
operation being audited fails. The alternative - log a warning and carry on - produces a system that
works perfectly while recording nothing, and the gap is found by whoever later needs the trail as
evidence. An audit event that is optional is not an audit trail.

**It opens no transaction of its own.** The project does not use `ATOMIC_REQUESTS`, so an ORM write
commits immediately and an event recorded during a request survives whatever happens next. A caller
that wraps its work in `transaction.atomic` and then rolls back *does* lose the event it recorded
inside, which is why a denial is recorded where the denial is decided rather than inside the
transaction that was refused. Stated rather than solved: solving it properly needs a second
connection, and that needs a better reason than a case which does not currently arise.

**The actor is snapshotted, not referenced.** The foreign key is set where an account exists, and
the username and role are copied as text in the same call. A rename must not restate
history, and a deactivated account must not orphan its events.

**The correlation identifier is never absent.** Inside a request it comes from `django_guid`;
outside one - a Celery task, a management command, a scheduled sweep - a fresh one is generated, so
the events of that unit of work still join to each other. A nullable column would be null in
precisely the cases that are hardest to investigate.

**`detail` holds names, not values**. That cannot be enforced by shape, so what is
enforced is narrower and mechanical: a key naming a credential is refused outright. The broader
prohibition - no filename, no note body, no evidential value - is a convention held by review and by
test, and this docstring is where a call site author is expected to have read it.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from django.db import models

from apps.audit.models import AuditAction, AuditEvent, AuditOutcome

if TYPE_CHECKING:
    from django.http import HttpRequest

#: The username recorded for work no person initiated: a scheduled sweep, a data migration, a worker
#: acting on its own schedule. A row with no actor and no username at all would violate
#: `ck_audit_auditevent_actor_identified`, and rightly: an event with no attribution is no record.
SYSTEM_ACTOR = "system"

#: The username recorded for a request that failed before any account was established - a login
#: attempt against a name that does not exist, for instance.
ANONYMOUS_ACTOR = "anonymous"

#: Keys that must never appear in `detail`. Refused rather than redacted: a call site passing one is
#: a defect, and silently dropping it would leave the author believing the value was recorded.
FORBIDDEN_DETAIL_KEYS = frozenset(
    {
        "password",
        "new_password",
        "old_password",
        "token",
        "access_token",
        "refresh_token",
        "secret",
        "secret_key",
        "api_key",
        "credential",
        "credentials",
        "connection_string",
        "session_key",
        "signature",
    }
)


class AuditDetailError(ValueError):
    """Raised when `detail` carries something that must not be recorded.

 A distinct type so that a test can assert the refusal without matching on message text, and so
 that nobody is tempted to catch `ValueError` broadly around a recorder call.
 """


def correlation_id() -> uuid.UUID:
    """The identifier of the current request, or a fresh one outside a request.

 `django_guid` stores the value in a context variable, so this works in a view, in middleware and
 in a Celery task the middleware propagated it to. Outside all of those it returns nothing, and a
 new identifier is generated rather than a null recorded.
 """
    from django_guid import get_guid

    raw = get_guid()
    if not raw:
        return uuid.uuid4()
    try:
        return uuid.UUID(raw)
    except (ValueError, AttributeError, TypeError):
        # `VALIDATE_GUID` is on, so this should be unreachable through a request. It is handled
        # anyway because the alternative is an audit write failing on a malformed header, which
        # would turn a logging concern into an outage.
        return uuid.uuid4()


def validate_detail(detail: Mapping[str, Any] | None) -> dict[str, Any]:
    """Checks the shallow rule and returns a plain dict.

 Only the top level is inspected. A recursive search would be more thorough and would also invite
 the belief that `detail` is sanitised, which it is not: the rule that matters - names and not
 values - is unenforceable and is held by review.
 """
    if detail is None:
        return {}

    offending = sorted(set(detail) & FORBIDDEN_DETAIL_KEYS)
    if offending:
        raise AuditDetailError(
            f"audit detail may not contain {offending}: the trail records what changed, never the "
            "value it changed to"
        )
    return dict(detail)


def describe(target: models.Model | None) -> dict[str, Any]:
    """Derives the target columns from a model instance.

 The target is generic rather than a foreign key, because the trail records events about
 entities that may not exist in the schema - a signed URL, a session, a login attempt. Where the
 target *is* a row, its numeric key is recorded too, purely so the trail can be joined.
 """
    if target is None:
        return {}

    described: dict[str, Any] = {
        "target_type": target._meta.model_name or "",
        "target_id": target.pk,
    }
    public_id = getattr(target, "public_id", None)
    if public_id is not None:
        described["target_public_id"] = public_id
    return described


def record(
    action: AuditAction | str,
    *,
    outcome: AuditOutcome | str = AuditOutcome.SUCCEEDED,
    actor: Any = None,
    actor_username: str | None = None,
    request: HttpRequest | None = None,
    target: models.Model | None = None,
    target_type: str = "",
    target_label: str = "",
    detail: Mapping[str, Any] | None = None,
) -> AuditEvent:
    """Writes one event and returns it.

 `actor` and `request` are both optional and interact. If `actor` is given it wins; otherwise the
 request's user is used when there is one authenticated. A request with an anonymous user gives
 an event with no foreign key and `anonymous` as the username, which is the correct record of a
 request refused before an identity was established.

 `actor_username` overrides that default and exists for one case: a failed sign-in, where the
 name that was tried is worth recording even though no account may exist under it. It is ignored
 when `actor` is given, because the snapshot must match the account it refers to.

 `target` is a model instance; `target_type` is for the cases where there is no row: a session, a
 signed URL, a login attempt. Passing neither is legitimate for an event about the actor
 themselves, such as a sign-in.
 """
    if actor is None and request is not None:
        candidate = getattr(request, "user", None)
        if candidate is not None and getattr(candidate, "is_authenticated", False):
            actor = candidate

    fields: dict[str, Any] = {
        "action": action,
        "outcome": outcome,
        "correlation_id": correlation_id(),
        "detail": validate_detail(detail),
        "target_label": target_label,
    }

    if actor is not None:
        fields["actor"] = actor
        fields["actor_username"] = actor.get_username
        fields["actor_role"] = getattr(actor, "role", "")
    elif actor_username:
        fields["actor_username"] = actor_username[:150]
    else:
        fields["actor_username"] = ANONYMOUS_ACTOR if request is not None else SYSTEM_ACTOR

    if request is not None:
        fields["request_method"] = request.method or ""
        # Truncated rather than refused. A path over three hundred characters is a caller doing
        # something odd, and losing the tail of it is preferable to losing the event.
        fields["request_path"] = request.path[:300]
        fields["source_ip"] = client_ip(request)

    fields.update(describe(target))
    if target_type:
        fields["target_type"] = target_type

    return AuditEvent.objects.create(**fields)


def record_denial(
    action: AuditAction | str,
    *,
    request: HttpRequest | None = None,
    actor: Any = None,
    target: models.Model | None = None,
    target_type: str = "",
    detail: Mapping[str, Any] | None = None,
) -> AuditEvent:
    """A refused attempt.

 Separate from `record` with an argument, because "denied" is the outcome that matters most and
 the one a call site is most likely to forget to pass. A trail of successes is a usage log; the
 refusals are what an investigation reads first.
 """
    return record(
        action,
        outcome=AuditOutcome.DENIED,
        actor=actor,
        request=request,
        target=target,
        target_type=target_type,
        detail=detail,
    )


def record_failed_authentication(
    *, request: HttpRequest | None = None, attempted_username: str = "", reason: str = ""
) -> AuditEvent:
    """A sign-in that did not succeed, attributed to the name that was tried.

 The name is recorded even though no account may exist under it, because a series of attempts
 against one name is the pattern worth seeing and it is invisible if every failure is recorded as
 `anonymous`. No password, no partial password and no indication of which half was wrong is
 recorded - that is what `reason` is constrained to be, a short code rather than a message.
 """
    return record(
        AuditAction.LOGIN_FAILED,
        outcome=AuditOutcome.FAILED,
        request=request,
        actor_username=attempted_username,
        detail={"reason": reason} if reason else None,
    )


def client_ip(request: HttpRequest) -> str | None:
    """The peer address, taken from `REMOTE_ADDR` and not from `X-Forwarded-For`.

 A forwarded header is client-controlled unless a proxy that overwrites it is known to be in
 front, and trusting it would let a caller write any address into the trail. Where a trusted
 proxy is deployed, it is configured to set `REMOTE_ADDR` and this function does not change.
 """
    address = request.META.get("REMOTE_ADDR")
    return address or None
