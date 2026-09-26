"""Permission classes, and the reason there are so few of them.

`REST_FRAMEWORK["DEFAULT_PERMISSION_CLASSES"]` requires authentication, so an endpoint
whose permissions were overlooked is closed rather than open. What is here narrows that default
by role. Nothing here decides whether a caller may see a particular object: that is the
queryset's job (`apps/common/querysets.py`).

Object-level refusal is expressed as absence, not as denial. A permission class returning
`False` from `has_object_permission` produces `403`, and a `403` on an object outside the
caller's scope confirms that the object exists. So a view scopes its queryset, `get_object`
finds nothing, and DRF raises `404`. The caller cannot distinguish "not yours" from "not
there".

`has_object_permission` is used only to refuse an operation on an object the caller can
already see. "You may read this case but not close it" is a `403`, because the existence of
the case is not in question.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar

from django.http import Http404
from rest_framework import permissions

from apps.common.querysets import ScopedQuerySet

if TYPE_CHECKING:
    from django.db.models import Model, QuerySet
    from rest_framework.request import Request
    from rest_framework.views import APIView

SAFE_METHODS = frozenset(permissions.SAFE_METHODS)


class RoleRequired(permissions.BasePermission):
    """Base class for a role restriction. Declares nothing; a subclass names the roles.

 `allowed_roles` is a frozenset of role *values* rather than enumeration members, so that this
 module does not import from `apps.accounts` - the kernel rule again. The subclasses that do the
 naming live with the app whose routes they protect.
 """

    allowed_roles: ClassVar[frozenset[str]] = frozenset()
    def has_permission(self, request: Request, view: APIView) -> bool:
        user = request.user
        if not (user and user.is_authenticated and user.is_active):
            return False
        if not self.allowed_roles:
            # An empty set means the subclass forgot to declare its roles. Refusing is the only safe
            # reading: treating it as "any role" would turn a typo into an open endpoint.
            return False
        return getattr(user, "role", None) in self.allowed_roles


class ReadOnly(permissions.BasePermission):
    """Permits the safe methods and nothing else.

 Composed with a role class rather than duplicated into one: `IsAuditor & ReadOnly` reads as what
 it is, and DRF supports the operators.
 """

    def has_permission(self, request: Request, view: APIView) -> bool:
        return request.method in SAFE_METHODS


class IsOwner(permissions.BasePermission):
    """An operation permitted to the owner of the object and to nobody else.

 An object-level class, and legitimately so: the caller can already see the object - the queryset
 let it through - and what is being refused is the operation. That is a `403` and should be.
 """

    owner_field: ClassVar[str] = "owner"

    def has_object_permission(self, request: Request, view: APIView, obj: Any) -> bool:
        return getattr(obj, self.owner_field, None) == request.user


class DenyAll(permissions.BasePermission):
    """Refuses everything.

 Useful as an explicit statement in a router or a viewset that is registered but not yet
 implemented. A route with no permission class inherits the project default; a route with this
 one says that its absence of function is intended.
 """

    def has_permission(self, request: Request, view: APIView) -> bool:
        return False


def scoped_or_404(queryset: ScopedQuerySet, user: Any, **lookup: Any) -> Model:
    """Fetches one object from within the caller's scope, or raises `Http404`.

 The helper exists so that the behaviour is one call rather than a pattern people remember
 to follow. `Http404` and not `PermissionDenied`: a caller must not be able to tell an object it
 may not see from one that does not exist.

 `queryset.visible_to` is called here and not by the caller, so that forgetting to scope is not
 something a view can do while still looking correct.
 """
    try:
        return queryset.visible_to(user).get(**lookup)
    except queryset.model.DoesNotExist as absent:
        raise Http404 from absent


class ScopedQuerysetMixin:
    """For a viewset whose model is case-scoped.

 The queryset is narrowed in `get_queryset`, which is where puts it: every list, retrieve,
 update and delete on the view passes through this one method, so an object outside the caller's
 scope is not merely refused but absent - and `get_object` raises `404` without the view needing
 to think about it.
 """

    def get_queryset(self) -> QuerySet[Any]:
        queryset = super().get_queryset()  # type: ignore[misc]
        return queryset.visible_to(self.request.user)  # type: ignore[attr-defined]
