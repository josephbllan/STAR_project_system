from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from .models import User


@admin.register(User)
class ShoeRagUserAdmin(UserAdmin):
    """Present only for local convenience during phase 1. The administrative screens of
 are the supported surface; this is not exposed in production."""

    list_display = ("username", "email", "role", "is_active", "mfa_enforced")
    list_filter = ("role", "is_active", "mfa_enforced")
    # `UserAdmin.fieldsets` is typed as possibly `None` and as either a list or a tuple, so it is
    # normalised before being extended rather than concatenated with whichever it happens to be.
    fieldsets = [
        *(UserAdmin.fieldsets or []),
        (
            "ShoeRAG",
            {"fields": ("role", "mfa_enforced", "mfa_confirmed_at", "deactivated_at")},
        ),
    ]
