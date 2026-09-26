"""Request a report, render it, expire the file. The row outlives the file."""

from __future__ import annotations

import hashlib
from datetime import timedelta
from io import BytesIO

from django.utils import timezone
from fpdf import FPDF

from apps.audit.models import AuditAction
from apps.audit.recorder import record
from apps.common import storage
from apps.reporting.models import Report, ReportFormat, ReportStatus


def request_report(
    *, case, requested_by, fmt: str = ReportFormat.PDF, scope: dict | None = None
) -> Report:
    report = Report.objects.create(
        case=case,
        format=fmt,
        scope=scope or {},
        requested_by=requested_by,
    )
    record(AuditAction.EXPORT_REQUESTED, actor=requested_by, target=report)
    return report


def render_pdf(report: Report) -> bytes:
    pdf = FPDF
    pdf.add_page
    pdf.set_font("Helvetica", size=14)
    pdf.cell(0, 10, text=f"ShoeRAG report: {report.case.name}", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", size=11)
    pdf.cell(0, 8, text=f"Case {report.case.public_id}", new_x="LMARGIN", new_y="NEXT")
    pdf.cell(0, 8, text=f"Requested by {report.requested_by}", new_x="LMARGIN", new_y="NEXT")
    return pdf.output


def complete_report(report: Report) -> Report:
    report.status = ReportStatus.GENERATING
    report.save(update_fields=["status", "updated_at"])
    try:
        body = render_pdf(report)
        public_id = report.public_id
        key = storage.storage_key_for_report(public_id)
        storage.store(key, BytesIO(body))
        report.storage_key = key
        report.sha256 = hashlib.sha256(body).hexdigest()
        report.byte_size = len(body)
        report.status = ReportStatus.AVAILABLE
        report.available_at = timezone.now()
        report.expires_at = timezone.now() + timedelta(days=7)
        report.save()
        record(AuditAction.EXPORT_COMPLETED, actor=report.requested_by, target=report)
    except Exception as exc:
        report.status = ReportStatus.FAILED
        report.failure_reason = str(exc)[:200]
        report.save(update_fields=["status", "failure_reason", "updated_at"])
        record(AuditAction.EXPORT_FAILED, actor=report.requested_by, target=report)
        raise
    return report


def expire_due_reports() -> int:
    now = timezone.now()
    due = Report.objects.filter(status=ReportStatus.AVAILABLE, expires_at__lte=now)
    count = 0
    for report in due:
        if report.storage_key:
            storage.delete(report.storage_key)
        report.storage_key = None
        report.status = ReportStatus.EXPIRED
        report.save(update_fields=["storage_key", "status", "updated_at"])
        count += 1
    return count
