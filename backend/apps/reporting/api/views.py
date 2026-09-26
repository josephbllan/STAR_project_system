from rest_framework import serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.accounts.permissions import CanExport
from apps.audit.models import AuditAction
from apps.audit.recorder import record
from apps.cases.models import Case
from apps.common import storage
from apps.common.permissions import ScopedQuerysetMixin
from apps.reporting.models import Report, ReportFormat
from apps.reporting.services import request_report
from apps.tasks.dispatch import dispatch


class ReportSerializer(serializers.ModelSerializer):
    class Meta:
        model = Report
        fields = ("public_id", "format", "status", "available_at", "expires_at", "download_count")


class ReportViewSet(ScopedQuerysetMixin, viewsets.ModelViewSet):
    queryset = Report.objects.all().select_related("case")
    serializer_class = ReportSerializer
    permission_classes = [CanExport]
    lookup_field = "public_id"
    http_method_names = ["get", "post", "head", "options"]

    def perform_create(self, serializer):
        case = Case.objects.visible_to(self.request.user).get(
            public_id=self.request.data["case_public_id"]
        )
        report = request_report(
            case=case,
            requested_by=self.request.user,
            fmt=self.request.data.get("format", ReportFormat.PDF),
            scope=self.request.data.get("scope") or {},
        )
        dispatch(
            "reporting.render_report",
            {"report_id": report.pk, "target_public_id": str(report.public_id)},
            scope_token=str(report.public_id),
            requested_by=self.request.user,
            target_type="report",
            target_public_id=report.public_id,
        )
        serializer.instance = report

    @action(detail=True, methods=["post"], url_path="access-url")
    def access_url(self, request, public_id=None):
        report = self.get_object()
        if not report.storage_key:
            return Response({"detail": "not available"}, status=status.HTTP_409_CONFLICT)
        grant = storage.signed_url(report.storage_key)
        report.download_count += 1
        report.save(update_fields=["download_count", "updated_at"])
        record(AuditAction.EXPORT_DOWNLOADED, actor=request.user, target=report)
        return Response({"url": grant.url, "expires_at": grant.expires_at})
