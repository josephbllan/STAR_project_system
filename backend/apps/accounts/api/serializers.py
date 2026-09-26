from rest_framework import serializers

from apps.accounts.models import Role, User


class UserSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = (
            "public_id",
            "username",
            "first_name",
            "last_name",
            "email",
            "role",
            "mfa_enforced",
            "is_active",
        )
        read_only_fields = ("public_id", "mfa_enforced")


class LoginSerializer(serializers.Serializer):
    username = serializers.CharField()
    password = serializers.CharField(write_only=True)


def capabilities_for(user: User) -> list[str]:
    caps = ["auth.session"]
    if user.role in {Role.INVESTIGATOR, Role.ADMINISTRATOR}:
        caps += ["case.create", "evidence.register", "search.query", "review.rate"]
    if user.role in {Role.ANALYST, Role.ADMINISTRATOR}:
        caps += ["search.query"]
    if user.role in {Role.REVIEWER, Role.ADMINISTRATOR}:
        caps += ["review.rate", "review.approve", "export.request"]
    if user.role == Role.ADMINISTRATOR:
        caps += ["admin.users", "admin.corpora"]
    if user.role == Role.AUDITOR:
        caps += ["audit.read"]
    return caps
