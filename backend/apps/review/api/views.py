from rest_framework import serializers, viewsets
from rest_framework.response import Response

from apps.accounts.permissions import CanReview
from apps.cases.models import Result
from apps.common.permissions import ScopedQuerysetMixin
from apps.review.models import Approval, Note, Rating, Scope
from apps.review.services import (
    countersign_approval,
    decide_approval,
    record_note,
    record_rating,
    toggle_validation_approval,
    withdraw_approval,
)


class RatingSerializer(serializers.ModelSerializer):
    class Meta:
        model = Rating
        fields = ("id", "scope", "value")


class NoteSerializer(serializers.ModelSerializer):
    class Meta:
        model = Note
        fields = ("public_id", "scope", "body", "is_pinned")


class ApprovalSerializer(serializers.ModelSerializer):
    result_public_id = serializers.UUIDField(write_only=True)

    class Meta:
        model = Approval
        fields = ("public_id", "state", "evidence_label", "notes", "result_public_id")
        read_only_fields = ("public_id", "state")
        extra_kwargs = {
            "evidence_label": {"required": False, "allow_blank": True},
            "notes": {"required": False, "allow_blank": True},
        }


class RatingViewSet(ScopedQuerysetMixin, viewsets.ModelViewSet):
    queryset = Rating.objects.all()
    serializer_class = RatingSerializer
    permission_classes = [CanReview]
    http_method_names = ["get", "post", "head", "options"]

    def perform_create(self, serializer):
        result = Result.objects.get(public_id=self.request.data["result_public_id"])
        record_rating(
            author=self.request.user,
            value=serializer.validated_data["value"],
            scope=Scope.RESULT,
            result=result,
        )


class NoteViewSet(ScopedQuerysetMixin, viewsets.ModelViewSet):
    queryset = Note.objects.all()
    serializer_class = NoteSerializer
    permission_classes = [CanReview]
    http_method_names = ["get", "post", "head", "options"]

    def perform_create(self, serializer):
        result = Result.objects.get(public_id=self.request.data["result_public_id"])
        record_note(
            author=self.request.user,
            body=serializer.validated_data["body"],
            scope=Scope.RESULT,
            result=result,
        )


class ApprovalViewSet(ScopedQuerysetMixin, viewsets.ModelViewSet):
    queryset = Approval.objects.all().select_related("result")
    serializer_class = ApprovalSerializer
    permission_classes = [CanReview]
    lookup_field = "public_id"
    http_method_names = ["get", "post", "patch", "head", "options"]

    def perform_create(self, serializer):
        result = Result.objects.get(public_id=serializer.validated_data["result_public_id"])
        approval = toggle_validation_approval(result=result, actor=self.request.user)
        serializer.instance = approval

    def partial_update(self, request, *args, **kwargs):
        approval = self.get_object()
        action = request.data.get("action")
        if action == "decide":
            decide_approval(
                approval, decided_by=request.user, approved=bool(request.data.get("approved", True))
            )
        elif action == "countersign":
            countersign_approval(approval, countersigned_by=request.user)
        elif action == "withdraw":
            withdraw_approval(approval, withdrawn_by=request.user)
        return Response(ApprovalSerializer(approval).data)
