import pytest
from django_otp.plugins.otp_totp.models import TOTPDevice

from tests.factories.accounts import InvestigatorFactory

pytestmark = [pytest.mark.django_db]


def test_enrolment_then_totp_completes_an_investigator_session(api_client) -> None:
    user = InvestigatorFactory()
    login = api_client.post(
        "/api/v1/auth/login/",
        {"username": user.username, "password": "correct-horse-battery-staple"},
        format="json",
    )
    assert login.data["mfa_required"] is True
    assert login.data["enrol_required"] is True

    start = api_client.post("/api/v1/auth/mfa/totp/")
    assert start.status_code == 200
    device = TOTPDevice.objects.get(user=user, confirmed=False)
    from django_otp.oath import totp

    token = f"{totp(device.bin_key):06d}"
    confirm = api_client.post("/api/v1/auth/mfa/totp/confirm/", {"code": token}, format="json")
    assert confirm.status_code == 200
    assert len(confirm.data["recovery_codes"]) == 10

    session = api_client.get("/api/v1/auth/session/")
    assert session.status_code == 200
    assert session.data["user"]["username"] == user.username


def test_an_invalid_totp_is_refused(api_client) -> None:
    user = InvestigatorFactory()
    api_client.post(
        "/api/v1/auth/login/",
        {"username": user.username, "password": "correct-horse-battery-staple"},
        format="json",
    )
    api_client.post("/api/v1/auth/mfa/totp/")
    refused = api_client.post("/api/v1/auth/mfa/totp/confirm/", {"code": "000000"}, format="json")
    assert refused.status_code == 401
