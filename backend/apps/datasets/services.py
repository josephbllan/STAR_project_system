"""Ingestion order: hash, store the original, record metadata, audit, then derive artefacts.

Writing a thumbnail first would leave a derived file with no content row if the process died
between the two writes.
"""

from __future__ import annotations

import hashlib
from io import BytesIO
from uuid import uuid4

from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.audit.models import AuditAction
from apps.audit.recorder import record
from apps.common import storage
from apps.common.images import thumbnail
from apps.common.validators import ImageRejectedError, inspect_image
from apps.datasets.models import (
    ContentObject,
    Corpus,
    DerivedArtifact,
    DigestVerification,
    EvidenceFile,
    EvidenceState,
    IntegrityState,
    Mount,
    ScanStatus,
    VerificationOutcome,
)


class AlreadyRegisteredError(Exception):
    def __init__(self, evidence: EvidenceFile) -> None:
        self.evidence = evidence
        super().__init__("already registered")


def register_bytes(
    data: bytes,
    *,
    corpus: Corpus,
    original_filename: str = "",
    source_path: str = "",
    mount=None,
    case=None,
    registered_by=None,
    derive: bool = True,
) -> EvidenceFile:
    digest = hashlib.sha256(data).hexdigest()
    inspected = inspect_image(data)

    with transaction.atomic():
        existing_content = ContentObject.objects.filter(sha256=digest).first()
        created = existing_content is None
        if existing_content is None:
            public_id = uuid4()
            key = storage.storage_key_for_content(public_id)
            storage.store(key, BytesIO(data))
            content = ContentObject.objects.create(
                public_id=public_id,
                sha256=digest,
                byte_size=len(data),
                media_type=inspected.media_type,
                width=inspected.width,
                height=inspected.height,
                storage_key=key,
                integrity_state=IntegrityState.UNVERIFIED,
            )
        else:
            content = existing_content
        try:
            with transaction.atomic():
                evidence = EvidenceFile.objects.create(
                    content=content,
                    corpus=corpus,
                    mount=mount,
                    case=case,
                    original_filename=original_filename,
                    source_path=source_path,
                    state=EvidenceState.REGISTERED,
                    registered_by=registered_by,
                )
        except IntegrityError as exc:
            existing = EvidenceFile.objects.filter(corpus=corpus, content=content).first()
            if existing is None:
                raise
            raise AlreadyRegisteredError(existing) from exc

        record(
            AuditAction.EVIDENCE_REGISTERED,
            actor=registered_by,
            target=evidence,
            detail={"corpus": corpus.code, "created_content": created},
        )

    if derive:
        derive_thumbnail(evidence, data)
    return evidence


def derive_thumbnail(evidence: EvidenceFile, data: bytes | None = None) -> DerivedArtifact:
    if data is None:
        with storage.open_stored(evidence.content.storage_key) as handle:
            data = handle.read()
    produced = thumbnail(data)
    digest = hashlib.sha256(produced).hexdigest()
    public_id = uuid4()
    key = storage.storage_key_for_artifact(public_id, "thumbnail")
    storage.store(key, BytesIO(produced))
    artifact, _ = DerivedArtifact.objects.get_or_create(
        source_file=evidence,
        kind="thumbnail",
        defaults={
            "public_id": public_id,
            "operation": "pillow.thumbnail",
            "parameters": {"size": [256, 256]},
            "sha256": digest,
            "byte_size": len(produced),
            "media_type": "image/webp",
            "storage_key": key,
            "width": 256,
            "height": 256,
        },
    )
    return artifact


def verify_content(content: ContentObject, *, task_run=None) -> DigestVerification:
    expected = content.sha256
    try:
        with storage.open_stored(content.storage_key) as handle:
            observed = hashlib.sha256(handle.read()).hexdigest()
        outcome = (
            VerificationOutcome.MATCH if observed == expected else VerificationOutcome.MISMATCH
        )
    except Exception:
        observed = None
        outcome = VerificationOutcome.UNREADABLE

    verification = DigestVerification.objects.create(
        content=content,
        expected_sha256=expected,
        observed_sha256=observed,
        outcome=outcome,
        task_run=task_run,
    )
    content.last_verified_at = verification.verified_at
    if outcome == VerificationOutcome.MATCH:
        content.integrity_state = IntegrityState.VERIFIED
        action = AuditAction.INTEGRITY_VERIFIED
    elif outcome == VerificationOutcome.MISMATCH:
        content.integrity_state = IntegrityState.MISMATCH
        EvidenceFile.objects.filter(content=content).update(state=EvidenceState.QUARANTINED)
        action = AuditAction.INTEGRITY_FAILED
    else:
        content.integrity_state = IntegrityState.UNREADABLE
        action = AuditAction.INTEGRITY_FAILED
    content.save(update_fields=["last_verified_at", "integrity_state", "updated_at"])
    record(action, target=content, detail={"outcome": outcome})
    if outcome == VerificationOutcome.MISMATCH:
        record(
            AuditAction.EVIDENCE_QUARANTINED, target=content, detail={"reason": "digest-mismatch"}
        )
    return verification


def scan_local_mount(mount: Mount, *, registered_by=None) -> dict[str, int]:
    """Walk a local folder (and every subfolder) and register each image into the mount's corpus."""
    root = storage.resolve_local_folder(mount.path)
    seen = 0
    errors = 0
    registered = 0
    already = 0
    for path in storage.iter_local_images(root):
        seen += 1
        try:
            register_bytes(
                storage.read_local_bytes(path),
                corpus=mount.corpus,
                original_filename=path.name[:512],
                source_path=str(path)[:1024],
                mount=mount,
                registered_by=registered_by,
            )
            registered += 1
        except AlreadyRegisteredError:
            already += 1
        except (ImageRejectedError, OSError, ValueError):
            errors += 1
    if seen == 0 or (errors and registered == 0 and already == 0):
        status = ScanStatus.FAILED
    elif errors:
        status = ScanStatus.PARTIAL
    else:
        status = ScanStatus.OK
    mount.last_scan_at = timezone.now()
    mount.last_scan_files_seen = seen
    mount.last_scan_error_count = errors
    mount.last_scan_status = status
    mount.save(
        update_fields=[
            "last_scan_at",
            "last_scan_files_seen",
            "last_scan_error_count",
            "last_scan_status",
            "updated_at",
        ]
    )
    return {"seen": seen, "errors": errors, "registered": registered}


def request_corpus_encoding(corpus: Corpus, *, requested_by=None) -> list:
    from uuid import NAMESPACE_URL, uuid5

    from apps.search.models import Encoder
    from apps.tasks.dispatch import dispatch, requeue

    runs = []
    for encoder in Encoder.objects.filter(is_active=True):
        target = uuid5(NAMESPACE_URL, f"encode:{corpus.pk}:{encoder.pk}")
        params = {
            "corpus_id": corpus.pk,
            "encoder_id": encoder.pk,
            "target_public_id": str(target),
        }
        run, created = dispatch(
            "indexing.encode_corpus",
            params,
            scope_token=f"encode:{corpus.pk}:{encoder.pk}",
            requested_by=requested_by,
            target_type="encoder",
            target_public_id=target,
        )
        if not created and _orphaned_encode_corpus(run):
            requeue(run, params)
            created = True
        runs.append((run, created))
    return runs


def _orphaned_encode_corpus(run) -> bool:
    """Parent is still in-flight but no batch is running — dispatch never queued the rest."""
    from django.utils import timezone

    from apps.tasks.models import IN_FLIGHT_STATUSES, TaskRun

    if run.task_name != "indexing.encode_corpus":
        return False
    if run.status not in {status.value for status in IN_FLIGHT_STATUSES}:
        return False
    if TaskRun.objects.filter(
        task_name="indexing.encode_batch",
        status__in=[status.value for status in IN_FLIGHT_STATUSES],
    ).exists():
        return False
    if (run.progress_current or 0) > 0:
        return True
    if run.started_at is None:
        return False
    return (timezone.now() - run.started_at).total_seconds() > 30
