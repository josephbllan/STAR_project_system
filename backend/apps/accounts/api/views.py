from __future__ import annotations

import secrets

from django.conf import settings
from django.contrib.auth import authenticate, login, logout
from django.middleware.csrf import get_token
from django.utils import timezone
from django.views.decorators.csrf import ensure_csrf_cookie
from django_otp.plugins.otp_static.models import StaticDevice, StaticToken
from django_otp.plugins.otp_totp.models import TOTPDevice
from rest_framework import status, viewsets
from rest_framework.decorators import action, api_view, permission_classes
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response

from apps.accounts.api.serializers import LoginSerializer, UserSerializer, capabilities_for
from apps.accounts.models import EXPORT_CAPABLE_ROLES, Role, User
from apps.accounts.permissions import IsAdministrator
from apps.audit.models import AuditAction, AuditOutcome
from apps.audit.recorder import record
from apps.common.exceptions import problem

SESSION_MFA_PENDING = "mfa_pending_user_id"
RECOVERY_CODE_COUNT = 10


@api_view(["GET"])
@permission_classes([AllowAny])
@ensure_csrf_cookie
def csrf(request: Request) -> Response:
    get_token(request)
    return Response(status=status.HTTP_204_NO_CONTENT)


@api_view(["POST"])
@permission_classes([AllowAny])
def login_view(request: Request) -> Response:
    serializer = LoginSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    user = authenticate(
        request=request._request,
        username=serializer.validated_data["username"],
        password=serializer.validated_data["password"],
    )
    if user is None or not user.is_active:
        record(
            AuditAction.LOGIN_FAILED,
            outcome=AuditOutcome.DENIED,
            actor_username=serializer.validated_data["username"],
            request=request._request,
        )
        return problem(
            status_code=status.HTTP_401_UNAUTHORIZED,
            type_slug="invalid-credentials",
            title="Authentication failed",
            detail="Authentication failed.",
        )
    if user.mfa_enforced and settings.MFA_CHALLENGE_AFTER_PASSWORD:
        login(request._request, user)
        request._request.session[SESSION_MFA_PENDING] = str(user.pk)
        return Response(
            {
                "user": None,
                "mfa_required": True,
                "enrol_required": user.mfa_confirmed_at is None,
                "methods": ["totp", "recovery_code"],
            }
        )
    login(request._request, user)
    request._request.session.pop(SESSION_MFA_PENDING, None)
    record(AuditAction.LOGIN_SUCCEEDED, actor=user, request=request._request)
    return Response(
        {
            "user": UserSerializer(user).data,
            "mfa_required": False,
            "enrol_required": False,
            "capabilities": capabilities_for(user),
        }
    )


def _pending_user(request: Request) -> User | None:
    pending_id = request._request.session.get(SESSION_MFA_PENDING)
    if not pending_id:
        return None
    return User.objects.filter(pk=pending_id).first()


def _complete_mfa(request: Request, user: User) -> Response:
    request._request.session.pop(SESSION_MFA_PENDING, None)
    record(AuditAction.LOGIN_SUCCEEDED, actor=user, request=request._request)
    return Response(
        {
            "user": UserSerializer(user).data,
            "mfa_required": False,
            "enrol_required": False,
            "capabilities": capabilities_for(user),
        }
    )


def _verify_failed() -> Response:
    return problem(
        status_code=status.HTTP_401_UNAUTHORIZED,
        type_slug="mfa-invalid",
        title="Verification failed",
        detail="That code is not valid. Codes expire after 30 seconds.",
    )


@api_view(["POST"])
@permission_classes([AllowAny])
def mfa_verify(request: Request) -> Response:
    user = _pending_user(request)
    if user is None:
        return problem(
            status_code=status.HTTP_401_UNAUTHORIZED,
            type_slug="mfa-incomplete",
            title="Second factor required",
            detail="Sign in first.",
        )
    code = str(request.data.get("code") or request.data.get("token") or "").replace(" ", "")
    if not code:
        return _verify_failed()
    for device in TOTPDevice.objects.filter(user=user, confirmed=True):
        if device.verify_token(code):
            return _complete_mfa(request, user)
    for device in StaticDevice.objects.filter(user=user, confirmed=True):
        if device.verify_token(code):
            return _complete_mfa(request, user)
    return _verify_failed()


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def mfa_totp_start(request: Request) -> Response:
    user = request.user
    TOTPDevice.objects.filter(user=user, confirmed=False).delete()
    device = TOTPDevice.objects.create(user=user, name="authenticator", confirmed=False)
    return Response(
        {
            "secret": device.key,
            "otpauth_uri": device.config_url,
            "warning": "This key is shown once. It cannot be retrieved later.",
        }
    )


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def mfa_totp_confirm(request: Request) -> Response:
    user = request.user
    code = str(request.data.get("code") or request.data.get("token") or "")
    device = TOTPDevice.objects.filter(user=user, confirmed=False).order_by("-id").first()
    if device is None or not device.verify_token(code):
        return _verify_failed()
    device.confirmed = True
    device.save(update_fields=["confirmed"])
    user.mfa_enforced = True
    user.mfa_confirmed_at = timezone.now()
    user.save(update_fields=["mfa_enforced", "mfa_confirmed_at", "updated_at"])
    StaticDevice.objects.filter(user=user).delete()
    static = StaticDevice.objects.create(user=user, name="recovery", confirmed=True)
    codes = []
    for _ in range(RECOVERY_CODE_COUNT):
        token = secrets.token_hex(8)
        StaticToken.objects.create(device=static, token=token)
        codes.append(token)
    if request._request.session.get(SESSION_MFA_PENDING):
        request._request.session.pop(SESSION_MFA_PENDING, None)
        record(AuditAction.LOGIN_SUCCEEDED, actor=user, request=request._request)
    return Response({"recovery_codes": codes})


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def logout_view(request: Request) -> Response:
    record(AuditAction.LOGOUT, actor=request.user, request=request._request)
    logout(request._request)
    return Response(status=status.HTTP_204_NO_CONTENT)


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def session_view(request: Request) -> Response:
    pending = bool(request._request.session.get(SESSION_MFA_PENDING))
    if pending:
        return Response(
            {
                "user": UserSerializer(request.user).data,
                "capabilities": [],
                "mfa_required": True,
                "enrol_required": request.user.mfa_confirmed_at is None,
            }
        )
    return Response(
        {
            "user": UserSerializer(request.user).data,
            "capabilities": capabilities_for(request.user),
            "mfa_required": False,
            "enrol_required": False,
        }
    )


class UserViewSet(viewsets.ModelViewSet):
    queryset = User.objects.all().order_by("username")
    serializer_class = UserSerializer
    permission_classes = [IsAdministrator]
    lookup_field = "public_id"
    http_method_names = ["get", "post", "patch", "head", "options"]

    def perform_create(self, serializer):
        password = self.request.data.get("password")
        role = serializer.validated_data.get("role", Role.ANALYST)
        user = serializer.save(mfa_enforced=role in EXPORT_CAPABLE_ROLES)
        if password:
            user.set_password(password)
            user.save(update_fields=["password"])
        record(AuditAction.USER_CREATED, actor=self.request.user, target=user)

    @action(detail=True, methods=["post"])
    def role(self, request: Request, public_id=None) -> Response:
        user = self.get_object()
        new_role = request.data.get("role")
        if new_role not in Role.values:
            return problem(
                status_code=status.HTTP_400_BAD_REQUEST,
                type_slug="role-invalid",
                title="Role invalid",
                detail="That is not a recognised role.",
            )
        user.role = new_role
        user.mfa_enforced = new_role in {"administrator", "investigator", "reviewer"}
        user.save(update_fields=["role", "mfa_enforced", "updated_at"])
        record(AuditAction.ROLE_CHANGED, actor=request.user, target=user, detail={"role": new_role})
        return Response(UserSerializer(user).data)

    @action(detail=True, methods=["post"])
    def deactivate(self, request: Request, public_id=None) -> Response:
        user = self.get_object()
        user.is_active = False
        user.deactivated_at = timezone.now()
        user.save(update_fields=["is_active", "deactivated_at", "updated_at"])
        record(AuditAction.USER_DEACTIVATED, actor=request.user, target=user)
        return Response(UserSerializer(user).data)

    @action(detail=True, methods=["post"])
    def password(self, request: Request, public_id=None) -> Response:
        user = self.get_object()
        new_password = request.data.get("password")
        if not new_password:
            return problem(
                status_code=status.HTTP_400_BAD_REQUEST,
                type_slug="password-required",
                title="Password required",
                detail="Provide a new password.",
            )
        user.set_password(new_password)
        user.password_changed_at = timezone.now()
        user.save(update_fields=["password", "password_changed_at", "updated_at"])
        record(AuditAction.PASSWORD_CHANGED, actor=request.user, target=user)
        return Response(status=status.HTTP_204_NO_CONTENT)
