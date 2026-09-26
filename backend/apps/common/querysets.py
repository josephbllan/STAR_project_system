"""Case scoping: one rule, defined once.

Scoping is the primary authorisation control and the permission class is secondary. A permission
class forgotten on one view discloses another investigator's case; a queryset scoped in the
base class cannot, because there is nothing in it to disclose. Every collection here is filtered
before it is permission-checked, and the check exists to refuse an operation rather than to hide
a row.

An account sees a case it owns, or a case on which it holds an *active* membership. Everything
else is scoped by following the path to its case.

The Administrator's scope is every case. That exemption is one widening branch in this method,
reached only after the authentication, active-account and audit-only checks. Administrator
reads are audited like anyone else's; membership remains the mechanism for every other role.

The Auditor sees nothing: an empty scope rather than absent routes, so the rule can be tested.

This module does not import from an app. It asks the user object `may_access_cases` and
`sees_all_cases` rather than comparing against a role enumeration. `sees_all_cases` defaults
to `False` when the object cannot answer it, so an unknown user type is scoped rather than
exempted.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Self

from django.db import models
from django.db.models import Q

if TYPE_CHECKING:
    from django.contrib.auth.models import AbstractBaseUser


class ScopedQuerySet(models.QuerySet):
    """A queryset that can narrow itself to what an account is permitted to see.

 Subclasses set `case_paths`: the lookup paths from this model to the case it belongs to. `("",)`
 means the model *is* the case. `None` means the model is not case-scoped, and `visible_to`
 refuses to guess rather than quietly returning everything.

 There is more than one path because the review models need it. A note is attached to a case, a
 run, a query *or* a result, with three of the four columns null, so reaching its case
 means following whichever one is populated. Declaring all four and letting the join decide is
 both shorter and harder to get wrong than a discriminator lookup in Python.
 """

    #: The lookup paths from this model to `cases.Case`. `("",)` for `Case` itself.
    case_paths: tuple[str,...] | None = None

    #: The reverse accessor from a case to its memberships, and the field naming the owner. Strings
    #: rather than imports, because this module cannot import the `cases` app.
    owner_field: str = "owner"
    membership_accessor: str = "memberships"

    #: Whether a row whose case reference is null is *shared* rather than orphaned.
    #:
    #: True for exactly one model in this project, and the exception is load-bearing: an evidence
    #: file registered to a shared corpus has no case, and it must be visible to every
    #: authenticated account or nobody could search the shared corpora at all. Left False everywhere
    #: else, because for every other model a null case would be a defect and treating it as shared
    #: would turn that defect into a disclosure.
    shared_when_unscoped: bool = False

    def _clone(self, *args: Any, **kwargs: Any) -> Self:
        """Django's clone does not copy attributes set after construction.

 `QuerySet.all` and every filter go through `_clone`, and DRF lists call `.all` on the
 viewset queryset. Without this, `case_paths` reset to the class default of `None` and
 `visible_to` refused to guess — correctly, but then no collection endpoint could run.
 """
        clone = super()._clone(*args, **kwargs)
        clone.case_paths = self.case_paths
        clone.owner_field = self.owner_field
        clone.membership_accessor = self.membership_accessor
        clone.shared_when_unscoped = self.shared_when_unscoped
        return clone

    def visible_to(self, user: AbstractBaseUser | None) -> Self:
        """The set of rows `user` may see. Never raises for an ordinary refusal; returns nothing.

 An anonymous caller, an inactive account and an auditor all produce the empty set. That they
 produce the *same* empty set is the property depends on: a caller cannot tell from a
 collection response whether a row exists and is hidden, or does not exist.

 The four checks are ordered narrowest-first and the order is load-bearing. An administrator
 whose account has been deactivated sees nothing, because the active-account check precedes
 the exemption; reversing the two would leave a disabled administrator with full visibility,
 which is the one outcome deactivation exists to prevent.
 """
        if self.case_paths is None:
            raise TypeError(
                f"{type(self).__name__} sets no case_paths, so visible_to cannot know how this "
                "model relates to a case. Use ('',) if the model is the case, one or more lookup "
                "paths if it reaches one, and a named queryset method if it is not case-scoped."
            )

        if user is None or not getattr(user, "is_authenticated", False):
            return self.none()
        if not getattr(user, "is_active", True):
            return self.none()
        if not getattr(user, "may_access_cases", True):
            return self.none()
        if getattr(user, "sees_all_cases", False):
            return self.all()

        return self.filter(self.scope_condition(user)).distinct()

    def scope_condition(self, user: AbstractBaseUser) -> Q:
        """The condition alone, so that a caller composing a larger query can reuse it.

 Exposed because the alternative is people reimplementing "owned or a member of" inline, and
 the second implementation is the one that forgets `revoked_at IS NULL`.
 """
        if not self.case_paths:
            raise TypeError(f"{type(self).__name__} sets no case_paths")

        condition = Q()
        if self.shared_when_unscoped:
            for path in self.case_paths:
                if path:
                    condition |= Q(**{f"{path}__isnull": True})

        for path in self.case_paths:
            prefix = f"{path}__" if path else ""
            condition |= Q(**{f"{prefix}{self.owner_field}": user})
            condition |= Q(
                **{
                    f"{prefix}{self.membership_accessor}__user": user,
                    # A membership is revoked by setting a timestamp, never by deletion.
                    # Omitting this condition is the most consequential mistake available in this
                    # file: it would restore access to everyone whose access had ever been removed.
                    f"{prefix}{self.membership_accessor}__revoked_at__isnull": True,
                }
            )
        return condition


class ScopedManager(models.Manager.from_queryset(ScopedQuerySet)):  # type: ignore[misc]
    """The manager to put on a case-scoped model.

 Exists so that a model declares `objects = ScopedManager(case_paths=…)` and gets `visible_to`
 without each app defining a manager class, and so that the paths are stated in one place per
 model - next to the fields they traverse, where a field rename is visible.
 """

    def __init__(
        self,
        *,
        case_paths: tuple[str,...] | None = None,
        owner_field: str = "owner",
        membership_accessor: str = "memberships",
        shared_when_unscoped: bool = False,
    ) -> None:
        super().__init__()
        self._case_paths = case_paths
        self._owner_field = owner_field
        self._membership_accessor = membership_accessor
        self._shared_when_unscoped = shared_when_unscoped

    def get_queryset(self) -> ScopedQuerySet:
        queryset = super().get_queryset()
        queryset.case_paths = self._case_paths
        queryset.owner_field = self._owner_field
        queryset.membership_accessor = self._membership_accessor
        queryset.shared_when_unscoped = self._shared_when_unscoped
        return queryset

    def deconstruct(self) -> tuple[bool, str, None, tuple[Any,...], dict[str, Any]]:
        """So that `makemigrations` does not see a new manager every time it runs.

 Django serialises a model's managers only when `use_in_migrations` is set, which this one
 does not - but the method is defined anyway, because the failure it prevents is a migration
 generated for no reason and then committed, which is cheaper to prevent than to explain.
 """
        return (
            False,
            "apps.common.querysets.ScopedManager",
            None,
            (),
            {
                "case_paths": self._case_paths,
                "owner_field": self._owner_field,
                "membership_accessor": self._membership_accessor,
                "shared_when_unscoped": self._shared_when_unscoped,
            },
        )
