import pytest

from apps.reporting.models import ReportStatus
from apps.reporting.services import complete_report, request_report
from tests.factories.accounts import InvestigatorFactory
from tests.factories.cases import CaseFactory

pytestmark = [pytest.mark.django_db]


def test_a_rendered_report_is_addressable_and_hashed(tmp_storage) -> None:
    report = request_report(case=CaseFactory(), requested_by=InvestigatorFactory())
    complete_report(report)
    report.refresh_from_db()
    assert report.status == ReportStatus.AVAILABLE
    assert report.sha256
    assert len(report.sha256) == 64
    assert report.storage_key
