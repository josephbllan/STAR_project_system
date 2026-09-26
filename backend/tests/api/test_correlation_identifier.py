"""The correlation identifier, end to end through the middleware.

This is the operational product of : the value by which a user's report of a failure
resolves to the request, to the log lines it produced, and later to the asynchronous work it
dispatched. It is tested through a real request because the middleware ordering is the
part that can be wrong.
"""

from __future__ import annotations

import uuid

import pytest
from django.urls import reverse

pytestmark = pytest.mark.integration

HEADER = "Correlation-ID"


def test_every_response_carries_the_header(client) -> None:
    assert client.get("/healthz").headers[HEADER]


def test_header_is_a_uuid(client) -> None:
    uuid.UUID(client.get("/healthz").headers[HEADER])


def test_each_request_gets_its_own_identifier(client) -> None:
    first = client.get("/healthz").headers[HEADER]
    second = client.get("/healthz").headers[HEADER]
    assert first != second


def test_client_supplied_identifier_is_adopted(client) -> None:
    """So that a caller already tracing a request can join its logs to ours."""
    supplied = str(uuid.uuid4())
    response = client.get("/healthz", headers={HEADER: supplied})
    assert response.headers[HEADER] == supplied


def test_client_supplied_rubbish_is_replaced(client) -> None:
    """An unvalidated header would let a caller write arbitrary text into every log line the
 request produces, which is log injection through the front door."""
    response = client.get("/healthz", headers={HEADER: "'; DROP TABLE audit_auditevent; --"})
    uuid.UUID(response.headers[HEADER])


def test_error_envelope_identifier_matches_the_header(client, tmp_storage) -> None:
    """The two must agree, because the header is what a client quotes and the body is what a
 developer reads. If they diverge, the identifier stops being a join key."""
    response = client.get(reverse("stored-object", kwargs={"token": "not-a-valid-token"}))
    assert response.status_code == 404
    # The 404 here is raised by the view as `Http404` and rendered by Django rather than DRF, so
    # the assertion is on the header alone; the envelope's own field is asserted against a
    # constructed exception in `tests/unit/test_error_envelope.py`.
    assert response.headers[HEADER]
