from django.urls import include, path
from rest_framework.routers import SimpleRouter

from apps.accounts.api import views

router = SimpleRouter()
router.register("users", views.UserViewSet, basename="user")

urlpatterns = [
    path("auth/csrf/", views.csrf, name="auth-csrf"),
    path("auth/login/", views.login_view, name="auth-login"),
    path("auth/mfa/verify/", views.mfa_verify, name="auth-mfa-verify"),
    path("auth/mfa/totp/", views.mfa_totp_start, name="auth-mfa-totp-start"),
    path("auth/mfa/totp/confirm/", views.mfa_totp_confirm, name="auth-mfa-totp-confirm"),
    path("auth/logout/", views.logout_view, name="auth-logout"),
    path("auth/session/", views.session_view, name="auth-session"),
    path("", include(router.urls)),
]
