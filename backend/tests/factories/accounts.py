"""Users.

`django_get_or_create` is not used and `build` is not exposed: every user reaches the database,
because a user built in memory never meets `ck_accounts_user_mfa_required_roles` and a factory
that can produce a state the schema forbids is a factory that hides the constraint.
"""

from __future__ import annotations

import factory
from django.contrib.auth import get_user_model

from apps.accounts.models import EXPORT_CAPABLE_ROLES, Role


class UserFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = get_user_model()
        skip_postgeneration_save = True

    username = factory.Sequence(lambda n: f"user-{n}")
    role = Role.ANALYST
    # Derived rather than defaulted, so that overriding `role` cannot produce an investigator
    # without multi-factor enforcement and therefore an IntegrityError in the arrangement.
    mfa_enforced = factory.LazyAttribute(lambda user: user.role in EXPORT_CAPABLE_ROLES)

    @factory.post_generation
    def password(self, create: bool, extracted: str | None, **kwargs: object) -> None:
        if not create:
            return
        self.set_password(extracted or "correct-horse-battery-staple")
        self.save(update_fields=["password"])


class AdministratorFactory(UserFactory):
    role = Role.ADMINISTRATOR


class InvestigatorFactory(UserFactory):
    role = Role.INVESTIGATOR


class ReviewerFactory(UserFactory):
    role = Role.REVIEWER


class AuditorFactory(UserFactory):
    role = Role.AUDITOR
