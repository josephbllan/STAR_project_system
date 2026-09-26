"""The custom user model. This migration must be the first one applied to a fresh database:
substituting `AUTH_USER_MODEL` after `django.contrib.auth` has migrated is not recoverable
without dropping the database.
"""

from django.contrib.auth.models import AbstractUser
from django.contrib.auth.validators import UnicodeUsernameValidator
from django.db import models
from django.db.models import Q

from apps.common.models import PublicIdModel, TimeStampedModel


class Role(models.TextChoices):
    ADMINISTRATOR = "administrator", "Administrator"
    INVESTIGATOR = "investigator", "Investigator"
    ANALYST = "analyst", "Analyst"
    REVIEWER = "reviewer", "Reviewer"
    AUDITOR = "auditor", "Auditor"


#: Roles permitted to export. Multi-factor authentication is mandatory for each of
#: them, and the rule is held in the schema so that an administrative edit cannot bypass it.
EXPORT_CAPABLE_ROLES = (Role.ADMINISTRATOR, Role.INVESTIGATOR, Role.REVIEWER)

#: The roles that read the audit trail and nothing else. Expressed as a queryset
#: scope returning the empty set for every other collection, and never as an absence of routes: a
#: route that exists and returns nothing is testable, whereas a route that was never registered can
#: only be asserted by its absence from a URL configuration that someone may later add it to.
AUDIT_ONLY_ROLES = (Role.AUDITOR,)

#: The roles whose case scope is the whole system. Enumerated here rather than derived from
#: `is_superuser`, because `is_superuser` also disables every permission check in Django's admin and
#: the two grants must be separable: an administrator of this application is not necessarily an
#: operator of the database. Membership remains the mechanism for everyone else, and an
#: administrator's reads are audited like any other, which is what keeps this from being an
#: unobservable exemption.
UNSCOPED_ROLES = (Role.ADMINISTRATOR,)


class User(AbstractUser, TimeStampedModel, PublicIdModel):
    # Declared without `unique=True` so that the constraint carries the project's name
    #; uniqueness is asserted in Meta.constraints instead.
    username = models.CharField(
        max_length=150,
        validators=[UnicodeUsernameValidator],
        help_text="150 characters or fewer. Letters, digits and @/./+/-/_ only.",
    )
    role = models.CharField(max_length=20, choices=Role.choices, default=Role.ANALYST)
    mfa_enforced = models.BooleanField(default=False)
    mfa_confirmed_at = models.DateTimeField(null=True, blank=True)
    password_changed_at = models.DateTimeField(null=True, blank=True)
    # `is_active` remains authoritative; this column exists so the audit trail can state
    # when deactivation happened without joining to the event stream.
    deactivated_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["username"], name="uq_accounts_user_username"),
            models.UniqueConstraint(fields=["public_id"], name="uq_accounts_user_public_id"),
            models.CheckConstraint(
                condition=Q(role__in=[choice.value for choice in Role]),
                name="ck_accounts_user_role_valid",
            ),
            models.CheckConstraint(
                condition=~Q(role__in=[role.value for role in EXPORT_CAPABLE_ROLES])
                | Q(mfa_enforced=True),
                name="ck_accounts_user_mfa_required_roles",
            ),
            models.CheckConstraint(
                condition=Q(mfa_confirmed_at__isnull=True) | Q(mfa_enforced=True),
                name="ck_accounts_user_mfa_confirmed_state",
            ),
        ]
        indexes = [
            models.Index(
                fields=["role"],
                condition=Q(is_active=True),
                name="ix_accounts_user_role_active",
            ),
        ]

    def __str__(self) -> str:
        return self.username

    @property
    def can_export(self) -> bool:
        return self.role in EXPORT_CAPABLE_ROLES

    @property
    def may_access_cases(self) -> bool:
        """Whether this account can see case-scoped data at all, before membership is considered.

 Asked by `apps.common.querysets.ScopedQuerySet`, which is in the shared kernel and must not
 import from an app. Putting the question on the user model rather than the role enumeration
 in `common` keeps the role policy where roles live, and keeps the kernel free of knowledge
 about the five particular roles this system happens to have.
 """
        return self.role not in AUDIT_ONLY_ROLES

    @property
    def sees_all_cases(self) -> bool:
        """Whether this account's case scope is the whole system, membership notwithstanding.

 The second of the two questions `ScopedQuerySet` asks a user, and it is a question rather
 than a role comparison for the same reason as the first: the shared kernel does not know
 what a role is. Answering `True` widens a scope, so the property is deliberately narrow - it
 consults the role field only, never `is_staff` or `is_superuser`, so that a Django-level
 grant made for an unrelated reason cannot widen case visibility as a side effect.
 """
        return self.role in UNSCOPED_ROLES
