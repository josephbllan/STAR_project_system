from apps.common.permissions import RoleRequired


class IsAdministrator(RoleRequired):
    allowed_roles = frozenset({"administrator"})


class IsInvestigator(RoleRequired):
    allowed_roles = frozenset({"investigator", "administrator"})


class CanSearch(RoleRequired):
    allowed_roles = frozenset({"investigator", "analyst", "administrator"})


class CanReview(RoleRequired):
    allowed_roles = frozenset({"reviewer", "investigator", "administrator"})


class CanExport(RoleRequired):
    allowed_roles = frozenset({"investigator", "reviewer", "administrator"})


class IsAuditor(RoleRequired):
    allowed_roles = frozenset({"auditor", "administrator"})
