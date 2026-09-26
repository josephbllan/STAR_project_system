from rest_framework import serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.tasks.lifecycle import request_cancellation
from apps.tasks.models import TaskRun


class TaskRunSerializer(serializers.ModelSerializer):
    class Meta:
        model = TaskRun
        fields = (
            "public_id",
            "task_name",
            "status",
            "phase",
            "progress_current",
            "progress_total",
            "queued_at",
            "started_at",
            "finished_at",
        )


class TaskRunViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = TaskRunSerializer
    permission_classes = [IsAuthenticated]
    lookup_field = "public_id"

    def get_queryset(self):
        qs = TaskRun.objects.all().order_by("-queued_at")
        user = self.request.user
        if getattr(user, "role", None) != "administrator":
            qs = qs.filter(requested_by=user)
        names = self.request.query_params.getlist("task_name")
        if len(names) == 1 and "," in names[0]:
            names = [part.strip() for part in names[0].split(",") if part.strip()]
        if names:
            qs = qs.filter(task_name__in=names)
        return qs

    @action(detail=True, methods=["post"])
    def cancel(self, request, public_id=None):
        run = self.get_object()
        request_cancellation(run)
        return Response(TaskRunSerializer(run).data, status=status.HTTP_202_ACCEPTED)
