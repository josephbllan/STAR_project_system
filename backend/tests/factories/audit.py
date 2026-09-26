"""Audit events.

The factory supplies both forms of attribution - the foreign key and the text snapshot - derived
from one another, because `ck_audit_auditevent_actor_identified` requires at least one and requires both wherever an actor is known.
"""

from __future__ import annotations

from uuid import uuid4

import factory

from apps.audit.models import AuditAction, AuditEvent, AuditOutcome
from tests.factories.accounts import InvestigatorFactory


class AuditEventFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = AuditEvent

    public_id = factory.LazyFunction(uuid4)
    action = AuditAction.EVIDENCE_ACCESSED
    actor = factory.SubFactory(InvestigatorFactory)
    #: Derived from the actor rather than stated, so a test that overrides the actor gets a
    #: consistent snapshot and a test that sets `actor=None` has to supply the username itself.
    actor_username = factory.LazyAttribute(lambda o: o.actor.username if o.actor else "")
    actor_role = factory.LazyAttribute(lambda o: o.actor.role if o.actor else "")
    target_type = "evidencefile"
    target_public_id = factory.LazyFunction(uuid4)
    target_id = None
    target_label = ""
    correlation_id = factory.LazyFunction(uuid4)
    request_method = "GET"
    request_path = "/api/v1/evidence/"
    source_ip = "203.0.113.7"
    outcome = AuditOutcome.SUCCEEDED
    detail = factory.LazyFunction(dict)


class SystemAuditEventFactory(AuditEventFactory):
    """Scheduled work has no actor. The username snapshot carries the name of the process instead,
 which is what keeps the row an audit record rather than an unattributed line."""

    action = AuditAction.INTEGRITY_VERIFIED
    actor = None
    actor_username = "system"
    actor_role = ""
    request_method = ""
    request_path = ""
    source_ip = None


class AnonymousAuditEventFactory(AuditEventFactory):
    """A failed login before the username is even known to be real."""

    action = AuditAction.LOGIN_FAILED
    actor = None
    actor_username = "unknown-account"
    actor_role = ""
    outcome = AuditOutcome.FAILED
    request_method = "POST"
    request_path = "/api/v1/auth/login/"
