from rest_framework import serializers, viewsets

from apps.accounts.permissions import IsAuditor
from apps.audit.models import AuditAction, AuditEvent
from apps.audit.recorder import record


class AuditEventSerializer(serializers.ModelSerializer):
    class Meta:
        model = AuditEvent
        fields = (
            "public_id",
            "occurred_at",
            "action",
            "actor_username",
            "actor_role",
            "target_type",
            "target_public_id",
            "outcome",
        )


class AuditEventViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = AuditEvent.objects.all().order_by("-occurred_at")
    serializer_class = AuditEventSerializer
    permission_classes = [IsAuditor]
    lookup_field = "public_id"

    def list(self, request, *args, **kwargs):
        record(
            AuditAction.AUDIT_READ,
            actor=request.user,
            detail={"filters": sorted(request.query_params.keys())},
        )
        return super().list(request, *args, **kwargs)
