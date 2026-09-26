"""One error envelope for the whole API, and the registry that gives constraint names meaning.

Django REST Framework produces at least four failure shapes from the same application:
`{"detail":...}` from a permission failure, `{"field": [...]}` from a serializer, an unhandled
`IntegrityError` as a 500, and `Http404` from a lookup. A client cannot tell a validation
failure from a conflict without inspecting the shape. This module is the sole producer of error
responses, so there is one shape.

The constraint name is read from `IntegrityError.__cause__.diag.constraint_name`, which psycopg
populates from the PostgreSQL error fields. It is deliberately not parsed out of the message
text: the message is human-readable, localised and version-dependent, and parsing it is how a
handler like this silently stops working after an upgrade.

Three constraints resolve to 500 on purpose. They protect internal invariants that no
legitimate request can violate, and giving a client an error code for them would imply the
client had some way to avoid it.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Final

from django.conf import settings
from django.core.exceptions import PermissionDenied as DjangoPermissionDenied
from django.db import DatabaseError, IntegrityError, InterfaceError, OperationalError
from django.http import Http404
from rest_framework import status as http
from rest_framework.response import Response
from rest_framework.views import exception_handler as drf_exception_handler

logger = logging.getLogger(__name__)

PROBLEM_CONTENT_TYPE: Final = "application/problem+json"


class Disposition(StrEnum):
    """Why a constraint has the status code it has.

 Recorded alongside the status because `500` means three different things in this registry,
 and a reader needs to know which without consulting the specification.
 """

    #: The client did something it could have done differently. Told plainly.
    CLIENT_ERROR = "client-error"
    #: An invariant no request can violate. Reaching it is a defect in this system.
    INTERNAL_INVARIANT = "internal-invariant"
    #: Not an error at all. A service layer catches it and returns a success; if it reaches
    #: this handler, the service that should have caught it did not.
    HANDLED_BY_SERVICE = "handled-by-service"


@dataclass(frozen=True, slots=True)
class ConstraintOutcome:
    status: int
    disposition: Disposition
    #: The stable identifier a client and a monitoring system key on. `None` where the outcome
    #: is a 500, because there is nothing stable to promise about a defect.
    type_slug: str | None = None
    #: Written for a client. It references no column value, because this envelope is the one
    #: channel guaranteed to reach a caller and a leak here is a disclosure.
    detail: str = ""


def _client(status_code: int, slug: str, detail: str) -> ConstraintOutcome:
    return ConstraintOutcome(status_code, Disposition.CLIENT_ERROR, slug, detail)


def _internal(reason: str) -> ConstraintOutcome:
    return ConstraintOutcome(
        http.HTTP_500_INTERNAL_SERVER_ERROR, Disposition.INTERNAL_INVARIANT, None, reason
    )


def _service(reason: str) -> ConstraintOutcome:
    return ConstraintOutcome(
        http.HTTP_500_INTERNAL_SERVER_ERROR, Disposition.HANDLED_BY_SERVICE, None, reason
    )


#: Transcribed in full from, including entries for tables that do not exist
#: yet. The mapping is already decided there, and transcribing it once is less error-prone than
#: remembering to add a row while writing each of the remaining migrations. Completeness is
#: enforced in the other direction - from `pg_constraint` to this dictionary - by test, so an
#: entry that never acquires a constraint is harmless while a constraint without an entry fails
#: the build.
CONSTRAINT_REGISTRY: Final[dict[str, ConstraintOutcome]] = {
    # accounts
    "uq_accounts_user_username": _client(
        http.HTTP_409_CONFLICT, "username-not-unique", "That username is already taken."
    ),
    "uq_accounts_user_public_id": _internal(
        "A public identifier collision indicates a defect in identifier generation, not a "
        "request a client could have made differently."
    ),
    "ck_accounts_user_role_valid": _client(
        http.HTTP_400_BAD_REQUEST, "role-invalid", "That is not a recognised role."
    ),
    "ck_accounts_user_mfa_required_roles": _client(
        http.HTTP_422_UNPROCESSABLE_ENTITY,
        "mfa-required-for-role",
        "This role permits export, so multi-factor authentication must be enforced on the "
        "account before the role can be assigned.",
    ),
    "ck_accounts_user_mfa_confirmed_state": _client(
        http.HTTP_422_UNPROCESSABLE_ENTITY,
        "mfa-confirmation-without-enforcement",
        "Multi-factor authentication cannot be confirmed on an account where it is not enforced.",
    ),
    # cases
    "uq_cases_casemembership_active": _client(
        http.HTTP_409_CONFLICT,
        "membership-already-active",
        "That user already has active membership of this case.",
    ),
    "ck_cases_casemembership_access_valid": _client(
        http.HTTP_400_BAD_REQUEST, "access-level-invalid", "That is not a recognised access level."
    ),
    "ck_cases_casemembership_revocation_pair": _internal(
        "A revocation without a revoking actor is a defect in the revoking service."
    ),
    "uq_cases_case_owner_name": _client(
        http.HTTP_409_CONFLICT, "case-name-not-unique", "You already own a case with this name."
    ),
    "uq_cases_run_case_label": _client(
        http.HTTP_409_CONFLICT,
        "run-label-not-unique",
        "This case already has a run with that label.",
    ),
    "uq_cases_result_query_file": _client(
        http.HTTP_409_CONFLICT,
        "duplicate-result",
        "That result is already recorded for this query.",
    ),
    "uq_cases_case_public_id": _internal(
        "A public identifier collision indicates a defect in identifier generation."
    ),
    "ck_cases_case_status_valid": _client(
        http.HTTP_400_BAD_REQUEST, "case-status-invalid", "That is not a recognised case status."
    ),
    "ck_cases_case_closed_at_paired": _internal(
        "A closed case without a closing time, or a closing time on a case that is open, is a "
        "defect in whichever transition wrote the status without the timestamp."
    ),
    "uq_cases_run_public_id": _internal(
        "A public identifier collision indicates a defect in identifier generation."
    ),
    "ck_cases_run_weights_range": _client(
        http.HTTP_422_UNPROCESSABLE_ENTITY,
        "fusion-weight-out-of-range",
        "A fusion weight is a share and must be between zero and one inclusive.",
    ),
    "ck_cases_run_top_k_range": _client(
        http.HTTP_422_UNPROCESSABLE_ENTITY,
        "top-k-out-of-range",
        "The requested result count must be between one and five hundred.",
    ),
    "ck_cases_run_overfetch_sane": _client(
        http.HTTP_422_UNPROCESSABLE_ENTITY,
        "overfetch-out-of-range",
        "The over-fetch factor must be between one and twenty, and its ceiling between one and "
        "five thousand.",
    ),
    "ck_cases_run_status_valid": _internal(
        "Run status is written by the dispatcher and the retrieval worker, never by a request."
    ),
    "ck_cases_run_encoder_present": _client(
        http.HTTP_422_UNPROCESSABLE_ENTITY,
        "no-encoder-selected",
        "A run needs at least one encoder; with none it can produce no score.",
    ),
    "uq_cases_runcorpus_run_corpus": _client(
        http.HTTP_409_CONFLICT,
        "corpus-already-selected",
        "That collection is already among the run's restrictions.",
    ),
    "uq_cases_query_run_sequence": _internal(
        "The sequence within a run is assigned by the service, not supplied by the client."
    ),
    "uq_cases_query_public_id": _internal(
        "A public identifier collision indicates a defect in identifier generation."
    ),
    "ck_cases_query_text_length": _client(
        http.HTTP_422_UNPROCESSABLE_ENTITY,
        "query-text-length",
        "A text query must be between one and two thousand characters.",
    ),
    "ck_cases_query_status_valid": _internal(
        "Query status is written by the retrieval worker, never by a request."
    ),
    "uq_cases_result_query_rank": _internal(
        "Two results at the same rank is a defect in the ranking, which assigns them."
    ),
    "uq_cases_result_public_id": _internal(
        "A public identifier collision indicates a defect in identifier generation."
    ),
    "ck_cases_result_rank_positive": _internal(
        "Ranks are one-based and assigned by the ranking. A rank below one is a defect in it."
    ),
    "ck_cases_result_scores_range": _internal(
        "Cosine similarity is bounded, and fusing bounded inputs under weights in zero to one is "
        "itself bounded. A score outside the range is a defect in the fusion."
    ),
    "ck_cases_result_at_least_one_model_score": _internal(
        "A result with neither model score came from a run with no encoder, which "
        "ck_cases_run_encoder_present already prevents. Reaching this is a defect in retrieval."
    ),
    "ck_cases_query_type_payload": _client(
        http.HTTP_400_BAD_REQUEST,
        "query-payload-invalid",
        "An image query requires a probe image and no text; a text query requires text and no "
        "probe image.",
    ),
    # datasets
    "uq_datasets_corpus_code": _client(
        http.HTTP_409_CONFLICT, "corpus-code-not-unique", "A corpus already uses that code."
    ),
    "uq_datasets_corpus_name": _client(
        http.HTTP_409_CONFLICT, "corpus-name-not-unique", "A corpus already uses that name."
    ),
    "ck_datasets_corpus_classification_valid": _client(
        http.HTTP_400_BAD_REQUEST,
        "data-classification-invalid",
        "A corpus is classified either real or synthetic.",
    ),
    "uq_datasets_mount_path": _client(
        http.HTTP_409_CONFLICT,
        "mount-path-not-unique",
        "That path is already registered as a mount.",
    ),
    "ck_datasets_mount_scan_status_valid": _internal(
        "Scan status is written by the scan task, never by a request."
    ),
    "ck_datasets_mount_scan_counts_non_negative": _internal(
        "A negative file or error count is a defect in the scan task's accounting."
    ),
    "uq_datasets_contentobject_sha256": _service(
        "A digest collision means the content is already stored and registered. The "
        "ingestion service must resolve it to a success rather than raise."
    ),
    "uq_datasets_contentobject_storage_key": _internal(
        "The storage key is derived from the public identifier, so a collision here is the same "
        "defect as a public identifier collision and never something a client supplied."
    ),
    "uq_datasets_contentobject_public_id": _internal(
        "A public identifier collision indicates a defect in identifier generation."
    ),
    "ck_datasets_contentobject_sha256_hex": _internal(
        "The digest is computed by this system during ingestion. A value that is not "
        "lower-case hexadecimal means the computation or the write was wrong."
    ),
    "ck_datasets_contentobject_byte_size_positive": _client(
        http.HTTP_422_UNPROCESSABLE_ENTITY,
        "file-empty",
        "The file holds no bytes, so there is nothing to register.",
    ),
    "ck_datasets_contentobject_dimensions_paired": _internal(
        "Width and height are written together when the image is decoded. One without the other "
        "is a half-completed decode, which is a defect in the ingestion pipeline."
    ),
    "ck_datasets_contentobject_dimensions_bounded": _client(
        http.HTTP_422_UNPROCESSABLE_ENTITY,
        "image-dimensions-out-of-range",
        "The image's pixel dimensions exceed the limit this system will decode.",
    ),
    "ck_datasets_contentobject_integrity_state_valid": _internal(
        "Integrity state is written by the verification sweep, never by a request."
    ),
    "uq_datasets_evidencefile_corpus_content": _client(
        http.HTTP_409_CONFLICT,
        "evidence-already-registered",
        "This content is already registered in this corpus.",
    ),
    "uq_datasets_evidencefile_public_id": _internal(
        "A public identifier collision indicates a defect in identifier generation."
    ),
    "ck_datasets_evidencefile_state_valid": _internal(
        "Registration state is set by ingestion, indexing and the verification sweep. A request "
        "names a transition rather than a state, so an invalid value cannot originate with one."
    ),
    "uq_datasets_derivedartifact_storage_key": _internal(
        "As for content: the key is derived from the public identifier."
    ),
    "uq_datasets_derivedartifact_public_id": _internal(
        "A public identifier collision indicates a defect in identifier generation."
    ),
    "uq_datasets_derivedartifact_source_kind_params": _service(
        "Derivation is idempotent. A second attempt with the same parameters must "
        "return the artefact that already exists rather than fail."
    ),
    "ck_datasets_derivedartifact_kind_valid": _internal(
        "The kind is chosen by the derivation operation, and an unrecognised one means an "
        "operation was added without extending the enumeration."
    ),
    "ck_datasets_derivedartifact_sha256_hex": _internal(
        "The digest is computed over bytes this system has just written."
    ),
    "ck_datasets_derivedartifact_no_self_source": _internal(
        "An artefact derived from itself is a defect in the derivation service."
    ),
    "ck_datasets_digestverification_outcome_valid": _internal(
        "The outcome is decided by the verification sweep."
    ),
    "ck_datasets_digestverification_observed_present": _internal(
        "A verification claiming to have read bytes without recording their digest, or claiming "
        "the object was unreadable while recording one, is a defect in the sweep."
    ),
    # search
    "uq_search_encoder_identity": _client(
        http.HTTP_409_CONFLICT,
        "encoder-already-registered",
        "An encoder with that name, version and preprocessing version is already registered.",
    ),
    "ck_search_encoder_family_valid": _client(
        http.HTTP_400_BAD_REQUEST,
        "encoder-family-invalid",
        "An encoder belongs to the clip family or the dinov2 family.",
    ),
    "ck_search_encoder_dimensions_match_family": _client(
        http.HTTP_422_UNPROCESSABLE_ENTITY,
        "encoder-dimensions-mismatch",
        "A clip encoder produces 512 dimensions and a dinov2 encoder produces 384. The "
        "dimensionality given does not match the family given.",
    ),
    "uq_search_clipembedding_content_encoder": _service(
        "One vector per encoder per byte sequence, so a redelivered encoding task "
        "must resolve to the existing row. The encoding service upserts rather than inserts."
    ),
    "uq_search_dinov2embedding_content_encoder": _service(
        "As for the clip table: re-encoding is an update, not an accumulation."
    ),
    "ck_search_annindexbuild_recall_range": _internal(
        "Recall is a fraction, computed by the measurement harness. A figure outside zero to one "
        "is a defect in the measurement rather than in a request."
    ),
    "ck_search_annindexbuild_recall_paired": _internal(
        "A recall figure without the k it was measured at is not a measurement."
    ),
    # review
    "ck_review_rating_scope_target": _internal(
        "The scope and its target are derived from the route, never taken from the body, so a "
        "mismatch is a defect in the view that built the row."
    ),
    "ck_review_rating_value_range": _client(
        http.HTTP_422_UNPROCESSABLE_ENTITY,
        "rating-out-of-range",
        "A rating is a whole number from one to five.",
    ),
    "uq_review_rating_case_author": _service(
        ": a revised rating replaces the author's own row, so the service upserts. The "
        "previous value is recoverable from the audit trail."
    ),
    "uq_review_rating_run_author": _service("As for a rating on a case."),
    "uq_review_rating_query_author": _service("As for a rating on a case."),
    "uq_review_rating_result_author": _service("As for a rating on a case."),
    "uq_review_tag_type_label": _client(
        http.HTTP_409_CONFLICT,
        "tag-label-not-unique",
        "A tag of that type already uses that label.",
    ),
    "ck_review_tag_type_valid": _client(
        http.HTTP_400_BAD_REQUEST, "tag-type-invalid", "That is not a recognised kind of tag."
    ),
    "ck_review_tagassignment_scope_target": _internal(
        "As for a rating: the scope and its target come from the route."
    ),
    "uq_review_tagassignment_case_tag": _client(
        http.HTTP_409_CONFLICT, "tag-already-assigned", "That tag is already applied here."
    ),
    "uq_review_tagassignment_run_tag": _client(
        http.HTTP_409_CONFLICT, "tag-already-assigned", "That tag is already applied here."
    ),
    "uq_review_tagassignment_query_tag": _client(
        http.HTTP_409_CONFLICT, "tag-already-assigned", "That tag is already applied here."
    ),
    "uq_review_tagassignment_result_tag": _client(
        http.HTTP_409_CONFLICT, "tag-already-assigned", "That tag is already applied here."
    ),
    "ck_review_note_scope_target": _internal(
        "As for a rating: the scope and its target come from the route."
    ),
    "uq_review_note_public_id": _internal(
        "A public identifier collision indicates a defect in identifier generation."
    ),
    "ck_review_note_body_length": _client(
        http.HTTP_422_UNPROCESSABLE_ENTITY,
        "note-length",
        "A note must be between one and twenty thousand characters. Note that sanitisation "
        "happens before this is measured, so the stored length may differ from what was typed.",
    ),
    "ck_review_note_supersession_paired": _internal(
        "A superseded note without a superseding one, or the reverse, is a defect in the "
        "amendment path."
    ),
    "ck_review_note_no_self_supersede": _internal(
        "A note that supersedes itself is a defect in the amendment path."
    ),
    "uq_review_resultorder_case_result": _client(
        http.HTTP_409_CONFLICT,
        "result-already-ordered",
        "That result already has a position in this case's ordering.",
    ),
    "uq_review_resultorder_case_position": _internal(
        "Positions are assigned by the reordering service, which holds them in one transaction "
        "under a deferred constraint. Reaching this means the service left two rows at one "
        "position at commit."
    ),
    "uq_review_approval_result": _client(
        http.HTTP_409_CONFLICT,
        "approval-already-exists",
        "This result has already been approved.",
    ),
    "uq_review_approval_public_id": _internal(
        "A public identifier collision indicates a defect in identifier generation."
    ),
    "ck_review_approval_state_valid": _internal(
        "Approval state is written by the approval service, which names transitions rather than "
        "accepting states."
    ),
    "ck_review_approval_decision_paired": _internal(
        "A decision without a decider and a time is an act nobody is accountable for, and the "
        "service writes all three together."
    ),
    "ck_review_approval_countersign_paired": _internal("As above, for the countersignature."),
    "ck_review_approval_no_self_countersign": _client(
        http.HTTP_422_UNPROCESSABLE_ENTITY,
        "self-countersignature-refused",
        "An approval cannot be countersigned by the person who decided it.",
    ),
    "ck_review_approval_countersign_requires_decision": _client(
        http.HTTP_422_UNPROCESSABLE_ENTITY,
        "countersignature-before-decision",
        "There is no decision here to countersign yet.",
    ),
    "ck_review_approval_withdrawal_paired": _internal(
        "A withdrawal without an actor and a time is an act nobody is accountable for."
    ),
    # reporting
    "uq_reporting_report_public_id": _internal(
        "A public identifier collision indicates a defect in identifier generation."
    ),
    "uq_reporting_report_storage_key": _internal(
        "The key is derived from the public identifier, so a collision is the same defect."
    ),
    "ck_reporting_report_format_valid": _client(
        http.HTTP_400_BAD_REQUEST,
        "report-format-invalid",
        "That is not a format this system produces.",
    ),
    "ck_reporting_report_status_valid": _internal(
        "Report status is written by the export worker, never by a request."
    ),
    "ck_reporting_report_available_has_object": _internal(
        "A report offered for download with no stored object behind it is a defect in the export "
        "worker, which must write the key and the digest before the status."
    ),
    "ck_reporting_report_sha256_hex": _internal(
        "The digest is computed over a file this system has just written."
    ),
    # audit
    "uq_audit_auditevent_public_id": _internal(
        "A public identifier collision indicates a defect in identifier generation."
    ),
    "ck_audit_auditevent_action_valid": _internal(
        "An action outside the taxonomy is a defect in the recorder's call site. No "
        "request names an audit action."
    ),
    "ck_audit_auditevent_outcome_valid": _internal(
        "The outcome is decided by the recorder."
    ),
    "ck_audit_auditevent_actor_identified": _internal(
        "An event with neither an actor nor a username snapshot is not an audit record, and the "
        "recorder is what failed to establish one."
    ),
    # config
    "uq_config_setting_key": _client(
        http.HTTP_409_CONFLICT, "setting-key-exists", "That setting already exists."
    ),
    # tasks
    "uq_tasks_taskrun_public_id": _internal(
        "A public identifier collision indicates a defect in identifier generation."
    ),
    "ck_tasks_taskrun_status_valid": _internal(
        "Task status is set by the dispatcher and the worker, never by a request, so an invalid "
        "value is a defect in this system rather than something a client could have avoided."
    ),
    "ck_tasks_taskrun_progress_non_negative": _internal(
        "Progress counters are written by workers. A counter past its total means the completion "
        "election has already fired, which is a defect in the aggregation."
    ),
    "ck_tasks_taskrun_terminal_finished": _internal(
        "A terminal run without a finish time is a defect in whichever transition wrote the "
        "status without writing the timestamp."
    ),
    "uq_tasks_taskrun_idempotency_key": _service(
        "A repeated dispatch is not an error. The dispatching service returns the existing task "
        "run with 200 rather than creating a second one."
    ),
}


def problem(
    *,
    status_code: int,
    type_slug: str,
    title: str,
    detail: str,
    instance: str | None = None,
    errors: list[dict[str, Any]] | None = None,
) -> Response:
    """Builds the one envelope. Every field is present even when three of them are empty,
 because a client written against a shape that is sometimes extended is a client with a
 branch per endpoint."""
    base = getattr(settings, "PROBLEM_TYPE_BASE_URL", "https://shoerag.example/errors")
    body: dict[str, Any] = {
        "type": f"{base}/{type_slug}",
        "title": title,
        "status": status_code,
        "detail": detail,
        "instance": instance,
        "correlation_id": correlation_id(),
        "errors": errors or [],
    }
    return Response(body, status=status_code, content_type=PROBLEM_CONTENT_TYPE)


def correlation_id() -> str | None:
    """The value by which a user's report of a failure is joined to the logs and to the audit
 trail. Absent outside a request, which is not an error: a task raising this
 exception is not answering an HTTP request."""
    try:
        from django_guid import get_guid
    except ImportError:  # pragma: no cover - django-guid is a hard dependency
        return None
    return get_guid()


def _flatten_validation_errors(detail: Any, field: str = "") -> list[dict[str, Any]]:
    """Turns DRF's nested validation detail into a flat array a client can iterate.

 DRF returns a dict of lists, a list, or a bare string depending on where the failure was
 raised. A client should not have to know which.
 """
    errors: list[dict[str, Any]] = []
    if isinstance(detail, dict):
        for key, value in detail.items():
            errors.extend(
                _flatten_validation_errors(value, f"{field}.{key}" if field else str(key))
            )
    elif isinstance(detail, list):
        for item in detail:
            errors.extend(_flatten_validation_errors(item, field))
    else:
        errors.append(
            {
                "field": field or None,
                "code": getattr(detail, "code", "invalid"),
                "detail": str(detail),
            }
        )
    return errors


_TITLES: Final[dict[int, str]] = {
    http.HTTP_400_BAD_REQUEST: "Validation failed",
    http.HTTP_401_UNAUTHORIZED: "Authentication required",
    http.HTTP_403_FORBIDDEN: "Not permitted",
    http.HTTP_404_NOT_FOUND: "Not found",
    http.HTTP_405_METHOD_NOT_ALLOWED: "Method not permitted",
    http.HTTP_409_CONFLICT: "Conflict",
    http.HTTP_413_REQUEST_ENTITY_TOO_LARGE: "Payload too large",
    http.HTTP_415_UNSUPPORTED_MEDIA_TYPE: "Unsupported media type",
    http.HTTP_422_UNPROCESSABLE_ENTITY: "Refused",
    http.HTTP_423_LOCKED: "Account locked",
    http.HTTP_428_PRECONDITION_REQUIRED: "Multi-factor authentication required",
    http.HTTP_429_TOO_MANY_REQUESTS: "Rate limit exceeded",
    http.HTTP_500_INTERNAL_SERVER_ERROR: "Internal error",
    http.HTTP_503_SERVICE_UNAVAILABLE: "Service unavailable",
}

_DEFAULT_SLUGS: Final[dict[int, str]] = {
    http.HTTP_400_BAD_REQUEST: "validation-failed",
    http.HTTP_401_UNAUTHORIZED: "authentication-required",
    http.HTTP_403_FORBIDDEN: "not-permitted",
    http.HTTP_404_NOT_FOUND: "not-found",
    http.HTTP_405_METHOD_NOT_ALLOWED: "method-not-permitted",
    http.HTTP_409_CONFLICT: "conflict",
    http.HTTP_413_REQUEST_ENTITY_TOO_LARGE: "payload-too-large",
    http.HTTP_415_UNSUPPORTED_MEDIA_TYPE: "unsupported-media-type",
    http.HTTP_422_UNPROCESSABLE_ENTITY: "refused",
    http.HTTP_423_LOCKED: "account-locked",
    http.HTTP_428_PRECONDITION_REQUIRED: "mfa-required",
    http.HTTP_429_TOO_MANY_REQUESTS: "rate-limited",
    http.HTTP_500_INTERNAL_SERVER_ERROR: "internal-error",
    http.HTTP_503_SERVICE_UNAVAILABLE: "service-unavailable",
}

#: `500` says nothing beyond the correlation identifier. Anything the exception knew - a
#: traceback, a SQL fragment, a filesystem path - is for the log, not the response.
_OPAQUE_500_DETAIL: Final = (
    "The request could not be completed. Quote the correlation identifier when reporting this."
)


def constraint_name_of(exc: BaseException) -> str | None:
    """Reads the violated constraint from psycopg's structured diagnostics."""
    cause = exc.__cause__
    diagnostics = getattr(cause, "diag", None)
    return getattr(diagnostics, "constraint_name", None)


def handle_integrity_error(exc: IntegrityError, instance: str | None) -> Response:
    """Maps a constraint violation to its documented response, or to a logged 500.

 An unmapped constraint is a defect to be fixed and not absorbed, so it is logged with the
 name it violated - which is the whole reason the naming convention at exists.
 """
    name = constraint_name_of(exc)
    outcome = CONSTRAINT_REGISTRY.get(name or "")

    if outcome is None:
        logger.error(
            "Unmapped integrity error; constraint=%s has no registry entry", name, exc_info=exc
        )
        return _opaque_500(instance)

    if outcome.disposition is Disposition.CLIENT_ERROR:
        assert outcome.type_slug is not None
        return problem(
            status_code=outcome.status,
            type_slug=outcome.type_slug,
            title=_TITLES[outcome.status],
            detail=outcome.detail,
            instance=instance,
        )

    # Both remaining dispositions are defects in this system rather than in the request, and
    # both are logged with the reason the registry gives so that the log says which.
    logger.error(
        "Constraint %s reached the exception handler: %s", name, outcome.detail, exc_info=exc
    )
    return _opaque_500(instance)


def _opaque_500(instance: str | None) -> Response:
    return problem(
        status_code=http.HTTP_500_INTERNAL_SERVER_ERROR,
        type_slug=_DEFAULT_SLUGS[http.HTTP_500_INTERNAL_SERVER_ERROR],
        title=_TITLES[http.HTTP_500_INTERNAL_SERVER_ERROR],
        detail=_OPAQUE_500_DETAIL,
        instance=instance,
    )


def shoerag_exception_handler(exc: Exception, context: dict[str, Any]) -> Response | None:
    """Installed as `EXCEPTION_HANDLER`. The only producer of an error response in this API."""
    request = context.get("request")
    instance = getattr(request, "path", None)

    if isinstance(exc, IntegrityError):
        return handle_integrity_error(exc, instance)

    if isinstance(exc, OperationalError | InterfaceError):
        # 503 and not 500, and this is a requirement rather than a nicety: a database that is
        # unreachable or not yet ready is an expected condition and monitoring must be able to
        # tell it apart from an application defect.
        logger.warning("Database unreachable", exc_info=exc)
        return problem(
            status_code=http.HTTP_503_SERVICE_UNAVAILABLE,
            type_slug=_DEFAULT_SLUGS[http.HTTP_503_SERVICE_UNAVAILABLE],
            title=_TITLES[http.HTTP_503_SERVICE_UNAVAILABLE],
            detail="A dependency is unavailable. The request may be retried.",
            instance=instance,
        )

    response = drf_exception_handler(exc, context)

    if response is None:
        # Nothing DRF recognises. In DEBUG the traceback page is more useful than an envelope;
        # otherwise the envelope keeps the shape uniform across the whole status range, which
        # is the property asks for.
        if isinstance(exc, DatabaseError) or settings.DEBUG:
            return None
        logger.exception("Unhandled exception", exc_info=exc)
        return _opaque_500(instance)

    status_code = response.status_code
    errors: list[dict[str, Any]] = []
    detail = ""

    data = response.data
    if isinstance(data, dict) and set(data) == {"detail"}:
        detail = str(data["detail"])
    elif isinstance(exc, Http404 | DjangoPermissionDenied):
        detail = str(data)
    else:
        errors = _flatten_validation_errors(data)
        detail = "One or more fields were rejected."

    return problem(
        status_code=status_code,
        type_slug=_DEFAULT_SLUGS.get(status_code, "error"),
        title=_TITLES.get(status_code, "Error"),
        detail=detail,
        instance=instance,
        errors=errors,
    )
