import pytest
from django.utils import timezone

from apps.cases.services import create_case, grant_membership
from apps.reporting.models import ReportStatus
from apps.reporting.services import complete_report, expire_due_reports, request_report
from tests.factories.accounts import InvestigatorFactory, UserFactory

pytestmark = [pytest.mark.django_db]


def test_create_case_and_grant_membership() -> None:
    owner = InvestigatorFactory()
    case = create_case(owner=owner, name="Harrier")
    member = UserFactory()
    grant_membership(case=case, user=member, access_level="read", granted_by=owner)
    assert case.memberships.filter(user=member, revoked_at__isnull=True).exists()


def test_expired_reports_lose_their_file_not_their_row(tmp_storage) -> None:
    report = request_report(
        case=create_case(owner=InvestigatorFactory(), name="Export"),
        requested_by=InvestigatorFactory(),
    )
    complete_report(report)
    report.expires_at = timezone.now()
    report.save(update_fields=["expires_at"])
    assert expire_due_reports == 1
    report.refresh_from_db()
    assert report.status == ReportStatus.EXPIRED
    assert report.storage_key is None
    assert report.sha256
