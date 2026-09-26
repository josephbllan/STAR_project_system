from rest_framework import serializers, viewsets

from apps.accounts.permissions import IsAdministrator
from apps.audit.models import AuditAction
from apps.audit.recorder import record
from apps.config.models import Setting


class SettingSerializer(serializers.ModelSerializer):
    class Meta:
        model = Setting
        fields = ("id", "key", "value", "description", "updated_at")
        read_only_fields = ("id", "updated_at")


class SettingViewSet(viewsets.ModelViewSet):
    queryset = Setting.objects.all().order_by("key")
    serializer_class = SettingSerializer
    permission_classes = [IsAdministrator]
    http_method_names = ["get", "post", "patch", "head", "options"]

    def perform_create(self, serializer):
        setting = serializer.save(updated_by=self.request.user)
        record(
            AuditAction.SETTING_CHANGED,
            actor=self.request.user,
            detail={"key": setting.key},
        )

    def perform_update(self, serializer):
        setting = serializer.save(updated_by=self.request.user)
        record(
            AuditAction.SETTING_CHANGED,
            actor=self.request.user,
            detail={"key": setting.key},
        )
