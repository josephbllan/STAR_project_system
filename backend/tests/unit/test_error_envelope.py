"""The error envelope, and the constraint registry that feeds it.

The handler is exercised directly rather than through an endpoint, because there are no
endpoints yet and these are the defaults every future endpoint inherits. `IntegrityError` is
constructed with the same structure psycopg produces - a `__cause__` carrying `diag` - so the
handler is reading what it will read in production rather than a convenient stand-in.
"""

from __future__ import annotations

import pytest
from django.db import IntegrityError, OperationalError
from django.http import Http404
from rest_framework.exceptions import NotAuthenticated, PermissionDenied, ValidationError
from rest_framework.test import APIRequestFactory

from apps.common import exceptions as exc_module
from apps.common.exceptions import (
    CONSTRAINT_REGISTRY,
    PROBLEM_CONTENT_TYPE,
    Disposition,
    shoerag_exception_handler,
)

pytestmark = pytest.mark.unit

ENVELOPE_FIELDS = {"type", "title", "status", "detail", "instance", "correlation_id", "errors"}


class FakeDiagnostics:
    def __init__(self, constraint_name: str) -> None:
        self.constraint_name = constraint_name


def integrity_error(constraint: str) -> IntegrityError:
    """Mirrors psycopg's structure: the driver exception becomes `__cause__` and carries `diag`."""
    error = IntegrityError("duplicate key value violates unique constraint")
    cause = Exception("underlying driver error")
    cause.diag = FakeDiagnostics(constraint)  # type: ignore[attr-defined]
    error.__cause__ = cause
    return error


def context(path: str = "/api/v1/cases/") -> dict:
    return {"request": APIRequestFactory().post(path)}


def handle(error: Exception, path: str = "/api/v1/cases/"):
    return shoerag_exception_handler(error, context(path))


# ----------------------------------------------------------------------------------------
# One shape, whatever failed.
# ----------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "error",
    [
        ValidationError({"name": ["This field is required."]}),
        PermissionDenied,
        NotAuthenticated,
        Http404,
        integrity_error("uq_cases_case_owner_name"),
        integrity_error("uq_accounts_user_public_id"),
        OperationalError("connection refused"),
    ],
)
def test_every_failure_produces_the_same_fields(error: Exception) -> None:
    """A client parses one shape. Three of these fields are frequently empty, and that is the
 point: a shape that is sometimes extended is a client with a branch per endpoint."""
    response = handle(error)
    assert set(response.data) == ENVELOPE_FIELDS


@pytest.mark.parametrize(
    "error",
    [ValidationError("no"), PermissionDenied, integrity_error("uq_cases_case_owner_name")],
)
def test_content_type_is_problem_json(error: Exception) -> None:
    assert handle(error).content_type == PROBLEM_CONTENT_TYPE


def test_type_is_a_url_under_a_controlled_name(settings) -> None:
    settings.PROBLEM_TYPE_BASE_URL = "https://example.test/errors"
    response = handle(integrity_error("uq_cases_case_owner_name"))
    assert response.data["type"] == "https://example.test/errors/case-name-not-unique"


def test_instance_is_the_requested_path() -> None:
    assert (
        handle(PermissionDenied, path="/api/v1/evidence/").data["instance"] == "/api/v1/evidence/"
    )


def test_status_in_body_matches_status_of_response() -> None:
    """A body that disagreed with the status line would leave a client to choose which to trust."""
    response = handle(integrity_error("ck_accounts_user_mfa_required_roles"))
    assert response.data["status"] == response.status_code == 422


# ----------------------------------------------------------------------------------------
# Field errors.
# ----------------------------------------------------------------------------------------


def test_field_errors_are_flattened_into_an_array() -> None:
    """DRF returns a dict of lists, a list, or a bare string depending on where the failure was
 raised. A client should not have to know which."""
    response = handle(
        ValidationError({"name": ["This field is required."], "reference": ["Too long."]})
    )
    fields = {entry["field"] for entry in response.data["errors"]}
    assert fields == {"name", "reference"}
    assert all({"field", "code", "detail"} == set(entry) for entry in response.data["errors"])


def test_nested_field_errors_keep_their_path() -> None:
    response = handle(ValidationError({"filters": {"corpus": ["Unknown corpus."]}}))
    assert response.data["errors"][0]["field"] == "filters.corpus"


def test_field_error_carries_the_drf_code() -> None:
    """The code is what a client branches on; `detail` is prose and may be reworded."""
    response = handle(
        ValidationError({"name": [ValidationError("Taken.", code="unique").detail[0]]})
    )
    assert response.data["errors"][0]["code"] == "unique"


def test_permission_failure_carries_no_field_errors() -> None:
    assert handle(PermissionDenied).data["errors"] == []


# ----------------------------------------------------------------------------------------
# The constraint registry.
# ----------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("constraint", "expected_status", "expected_slug"),
    [
        ("uq_accounts_user_username", 409, "username-not-unique"),
        ("ck_accounts_user_mfa_required_roles", 422, "mfa-required-for-role"),
        ("ck_accounts_user_role_valid", 400, "role-invalid"),
        ("uq_cases_case_owner_name", 409, "case-name-not-unique"),
        ("uq_review_approval_result", 409, "approval-already-exists"),
        ("ck_review_approval_no_self_countersign", 422, "self-countersignature-refused"),
        ("uq_datasets_evidencefile_corpus_content", 409, "evidence-already-registered"),
    ],
)
def test_mapped_constraints_reach_their_documented_status(
    constraint: str, expected_status: int, expected_slug: str
) -> None:
    """`uq_cases_case_owner_name` arriving at a client as a 500 would be both a
 lie about whose fault it was and a disclosure of the schema."""
    response = handle(integrity_error(constraint))
    assert response.status_code == expected_status
    assert response.data["type"].endswith(f"/{expected_slug}")


def test_unmapped_constraint_is_a_logged_500(caplog) -> None:
    """An unmapped constraint is a defect to be fixed, not absorbed. The log records the name it
 violated, which is the reason the naming convention at exists."""
    with caplog.at_level("ERROR"):
        response = handle(integrity_error("uq_something_nobody_registered"))
    assert response.status_code == 500
    assert "uq_something_nobody_registered" in caplog.text


def test_internal_invariant_constraints_are_500_and_say_nothing() -> None:
    """These protect invariants no request can violate. A client-facing code would imply the
 client had a way to avoid it."""
    response = handle(integrity_error("ck_cases_casemembership_revocation_pair"))
    assert response.status_code == 500
    assert response.data["detail"] == exc_module._OPAQUE_500_DETAIL


def test_service_handled_constraints_are_500_if_they_reach_the_handler() -> None:
    """A repeated dispatch is not an error, but the service is what turns it into a success. If
 it reaches here, the service that should have caught it did not."""
    response = handle(integrity_error("uq_tasks_taskrun_idempotency_key"))
    assert response.status_code == 500


def test_registry_entries_are_internally_consistent() -> None:
    """A client-error entry without a slug or without a message would produce an envelope with a
 null `type` or an empty `detail`, which no test of a single constraint would catch."""
    for name, outcome in CONSTRAINT_REGISTRY.items():
        if outcome.disposition is Disposition.CLIENT_ERROR:
            assert outcome.type_slug, f"{name} has no type slug"
            assert outcome.detail, f"{name} has no client message"
            assert 400 <= outcome.status < 500, (
                f"{name} is a client error with status {outcome.status}"
            )
        else:
            assert outcome.status == 500, f"{name} is internal but not a 500"
            assert outcome.type_slug is None


def test_registry_messages_disclose_no_schema() -> None:
    """This envelope is the one channel guaranteed to reach a caller, so a
 message naming a table, a column or a constraint would be a disclosure through it."""
    for name, outcome in CONSTRAINT_REGISTRY.items():
        if outcome.disposition is not Disposition.CLIENT_ERROR:
            continue
        message = outcome.detail.lower()
        for leak in ("uq_", "ck_", "_id", "select ", "null", "constraint", "column", "table"):
            assert leak not in message, f"{name} discloses {leak!r}"


# ----------------------------------------------------------------------------------------
# Availability failures are not defects.
# ----------------------------------------------------------------------------------------


def test_unreachable_database_is_503_not_500() -> None:
    """A cold start or transient saturation is an expected condition, and
 monitoring must be able to tell it apart from an application defect."""
    assert handle(OperationalError("could not connect")).status_code == 503


def test_503_invites_a_retry_and_names_nothing() -> None:
    response = handle(OperationalError("could not connect to server at 127.0.0.1 port 5432"))
    assert "retried" in response.data["detail"]
    assert "5432" not in response.data["detail"]
    assert "127.0.0.1" not in response.data["detail"]
