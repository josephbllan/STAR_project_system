"""Exports.

`ReportFactory` defaults to `requested` with no stored object, which is the state a report is in the
moment it is asked for. A factory defaulting to `available` would have had to invent a key
and a digest, and every test would then have started from a state the request path never produces.
"""

from __future__ import annotations

from uuid import uuid4

import factory
from django.utils import timezone

from apps.common import storage
from apps.reporting.models import Report, ReportFormat, ReportStatus
from tests.factories.accounts import InvestigatorFactory
from tests.factories.cases import CaseFactory
from tests.factories.datasets import digest


class ReportFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Report

    public_id = factory.LazyFunction(uuid4)
    case = factory.SubFactory(CaseFactory)
    format = ReportFormat.PDF
    scope = factory.LazyFunction(dict)
    status = ReportStatus.REQUESTED
    storage_key = None
    sha256 = None
    byte_size = None
    requested_by = factory.SubFactory(InvestigatorFactory)
    task_run = None
    available_at = None
    expires_at = None


class AvailableReportFactory(ReportFactory):
    """The key, the digest and the status together, because
 `ck_reporting_report_available_has_object` refuses a report offered for download with nothing
 behind it."""

    status = ReportStatus.AVAILABLE
    storage_key = factory.LazyAttribute(lambda o: storage.storage_key_for_report(o.public_id))
    sha256 = factory.Sequence(digest)
    byte_size = 524_288
    available_at = factory.LazyFunction(timezone.now)
    expires_at = factory.LazyFunction(lambda: timezone.now() + timezone.timedelta(days=7))


class ExpiredReportFactory(ReportFactory):
    """The normal end state, not a broken one: the file has been removed and the record of
 the export outlives it. The digest is kept, so a copy produced later can still be checked."""

    status = ReportStatus.EXPIRED
    storage_key = None
    sha256 = factory.Sequence(digest)
    byte_size = 524_288
    available_at = factory.LazyFunction(timezone.now)
