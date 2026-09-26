import pytest

from tests.factories.accounts import InvestigatorFactory, UserFactory
from tests.factories.cases import CaseFactory

pytestmark = [pytest.mark.django_db]


def test_login_and_session(api_client) -> None:
    user = UserFactory  # analyst: MFA is not enforced
    response = api_client.post(
        "/api/v1/auth/login/",
        {"username": user.username, "password": "correct-horse-battery-staple"},
        format="json",
    )
    assert response.status_code == 200
    assert response.data["user"]["role"] == "analyst"
    session = api_client.get("/api/v1/auth/session/")
    assert session.status_code == 200
    assert session.data["user"]["username"] == user.username


def test_an_export_capable_role_is_challenged_for_a_second_factor(api_client) -> None:
    user = InvestigatorFactory()
    response = api_client.post(
        "/api/v1/auth/login/",
        {"username": user.username, "password": "correct-horse-battery-staple"},
        format="json",
    )
    assert response.status_code == 200
    assert response.data["mfa_required"] is True
    assert response.data["user"] is None


def test_local_bypass_completes_an_investigator_session(api_client, settings) -> None:
    settings.MFA_CHALLENGE_AFTER_PASSWORD = False
    user = InvestigatorFactory()
    response = api_client.post(
        "/api/v1/auth/login/",
        {"username": user.username, "password": "correct-horse-battery-staple"},
        format="json",
    )
    assert response.status_code == 200
    assert response.data["mfa_required"] is False
    assert response.data["user"]["role"] == "investigator"


def test_login_failure_does_not_name_the_account(api_client) -> None:
    response = api_client.post(
        "/api/v1/auth/login/",
        {"username": "nobody", "password": "wrong"},
        format="json",
    )
    assert response.status_code == 401
    assert "nobody" not in response.content.decode


def test_an_investigator_lists_only_their_cases(client_as) -> None:
    owner = InvestigatorFactory()
    outsider = InvestigatorFactory()
    CaseFactory(owner=owner, name="Mine")
    CaseFactory(owner=outsider, name="Theirs")
    client = client_as(owner)
    response = client.get("/api/v1/cases/")
    assert response.status_code == 200
    names = {row["name"] for row in response.data["results"]}
    assert names == {"Mine"}


def test_an_outsider_cannot_tell_a_case_exists(client_as) -> None:
    owner = InvestigatorFactory()
    outsider = InvestigatorFactory()
    case = CaseFactory(owner=owner)
    client = client_as(outsider)
    response = client.get(f"/api/v1/cases/{case.public_id}/")
    assert response.status_code == 404
