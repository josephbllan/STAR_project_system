import pytest

from tests.factories.accounts import AdministratorFactory, AuditorFactory, InvestigatorFactory
from tests.factories.cases import CaseFactory

pytestmark = [pytest.mark.django_db]


@pytest.mark.parametrize(
    ("factory", "path", "allowed"),
    [
        (InvestigatorFactory, "/api/v1/cases/", True),
        (AuditorFactory, "/api/v1/cases/", True),  # empty list, not 403
        (AdministratorFactory, "/api/v1/users/", True),
        (InvestigatorFactory, "/api/v1/users/", False),
        (AuditorFactory, "/api/v1/audit/", True),
        (InvestigatorFactory, "/api/v1/audit/", False),
    ],
)
def test_collection_visibility_by_role(client_as, factory, path, allowed) -> None:
    user = factory()
    if path.startswith("/api/v1/cases/"):
        CaseFactory(owner=user if user.role == "investigator" else InvestigatorFactory())
    response = client_as(user).get(path)
    if allowed:
        assert response.status_code == 200
    else:
        assert response.status_code in {403, 404}
