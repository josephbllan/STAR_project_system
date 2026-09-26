from django.db import IntegrityError
from django.db.models import Count
from django.utils import timezone
from rest_framework import serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.accounts.permissions import CanSearch, IsInvestigator
from apps.audit.models import AuditAction
from apps.audit.recorder import record
from apps.cases.models import Case, Query, QueryStatus, Result, Run
from apps.cases.services import (
    create_case,
    create_run,
    grant_membership,
    save_run,
    set_case_status,
    submit_query,
)
from apps.common.exceptions import problem
from apps.common.permissions import ScopedQuerysetMixin
from apps.datasets.models import ContentObject, Corpus
from apps.datasets.services import AlreadyRegisteredError, register_bytes


def _flag(value, default: bool = True) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() not in {"0", "false", "no", "off"}


class CaseSerializer(serializers.ModelSerializer):
    class Meta:
        model = Case
        fields = (
            "public_id",
            "name",
            "reference",
            "description",
            "status",
            "opened_at",
            "closed_at",
        )


class RunSerializer(serializers.ModelSerializer):
    query_count = serializers.IntegerField(read_only=True, default=0)
    saved = serializers.SerializerMethodField()

    class Meta:
        model = Run
        fields = (
            "public_id",
            "label",
            "status",
            "top_k",
            "model_weight",
            "metadata_weight",
            "created_at",
            "query_count",
            "saved",
        )

    def get_saved(self, obj: Run) -> bool:
        return bool((obj.params or {}).get("saved"))


class QuerySerializer(serializers.ModelSerializer):
    query_text = serializers.CharField(read_only=True, allow_null=True)
    probe_filename = serializers.SerializerMethodField()
    probe_evidence_id = serializers.SerializerMethodField()

    class Meta:
        model = Query
        fields = (
            "public_id",
            "sequence",
            "query_type",
            "status",
            "result_count",
            "routed_spectrum",
            "query_text",
            "probe_filename",
            "probe_evidence_id",
        )

    def _probe_evidence(self, obj: Query):
        if obj.probe_content_id:
            regs = list(obj.probe_content.registrations.all())
            case_id = getattr(obj.run, "case_id", None)
            if case_id:
                matched = next((row for row in regs if row.case_id == case_id), None)
                if matched:
                    return matched
            if regs:
                return regs[0]
        result = obj.results.order_by("rank").select_related("evidence_file").first()
        return result.evidence_file if result else None

    def get_probe_filename(self, obj: Query) -> str | None:
        evidence = self._probe_evidence(obj)
        return evidence.original_filename if evidence else None

    def get_probe_evidence_id(self, obj: Query) -> str | None:
        evidence = self._probe_evidence(obj)
        return str(evidence.public_id) if evidence else None


class ResultSerializer(serializers.ModelSerializer):
    evidence = serializers.SerializerMethodField()
    approval_state = serializers.SerializerMethodField()

    class Meta:
        model = Result
        fields = (
            "public_id",
            "rank",
            "score_fused",
            "score_model",
            "score_clip",
            "score_dinov2",
            "score_metadata",
            "evidence_file",
            "evidence",
            "approval_state",
        )

    def get_approval_state(self, obj: Result) -> str | None:
        approval = next(iter(obj.approvals.all()), None)
        return approval.state if approval else None

    def get_evidence(self, obj: Result) -> dict:
        file = obj.evidence_file
        return {
            "public_id": str(file.public_id),
            "original_filename": file.original_filename,
            "source_path": file.source_path,
            "corpus_code": file.corpus.code,
            "state": file.state,
            "thumbnail_access": f"/api/v1/evidence/{file.public_id}/access-url/",
        }


class CaseViewSet(ScopedQuerysetMixin, viewsets.ModelViewSet):
    queryset = Case.objects.all().select_related("owner").order_by("-opened_at")
    serializer_class = CaseSerializer
    lookup_field = "public_id"
    http_method_names = ["get", "post", "patch", "head", "options"]

    def get_permissions(self):
        if self.action == "create":
            return [IsInvestigator()]
        return [IsAuthenticated()]

    def perform_create(self, serializer):
        case = create_case(owner=self.request.user, **serializer.validated_data)
        serializer.instance = case

    @action(detail=True, methods=["post"])
    def status(self, request, public_id=None):
        case = self.get_object()
        set_case_status(case, status=request.data.get("status"), actor=request.user)
        return Response(CaseSerializer(case).data)

    @action(detail=True, methods=["post"])
    def members(self, request, public_id=None):
        case = self.get_object()
        from apps.accounts.models import User

        user = User.objects.get(public_id=request.data["user_public_id"])
        grant_membership(
            case=case,
            user=user,
            access_level=request.data.get("access_level", "read"),
            granted_by=request.user,
        )
        return Response(status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["get", "post"])
    def runs(self, request, public_id=None):
        case = self.get_object()
        if request.method == "GET":
            rows = case.runs.annotate(query_count=Count("queries")).order_by("-created_at")
            return Response(RunSerializer(rows, many=True).data)
        label = request.data.get("label") or f"search {timezone.now().strftime('%H:%M:%S.%f')}"
        payload = {
            "case": case,
            "created_by": request.user,
            "label": label,
            "top_k": int(request.data.get("top_k", 10)),
            "model_weight": request.data.get("model_weight", "0.500"),
            "metadata_weight": request.data.get("metadata_weight", "0.150"),
            "corpus_ids": request.data.get("corpus_ids") or [],
            "use_clip": _flag(request.data.get("use_clip"), True),
            "use_dinov2": _flag(request.data.get("use_dinov2"), True),
        }
        try:
            run = create_run(**payload)
        except IntegrityError:
            payload["label"] = f"{label}-{timezone.now().strftime('%f')}"
            run = create_run(**payload)
        return Response(RunSerializer(run).data, status=status.HTTP_201_CREATED)


class RunViewSet(ScopedQuerysetMixin, viewsets.ReadOnlyModelViewSet):
    queryset = Run.objects.all().select_related("case").annotate(query_count=Count("queries"))
    serializer_class = RunSerializer
    lookup_field = "public_id"

    @action(detail=True, methods=["get", "post"], permission_classes=[CanSearch])
    def queries(self, request, public_id=None):
        run = self.get_object()
        if request.method == "GET":
            rows = (
                run.queries.select_related("run", "probe_content")
                .prefetch_related("probe_content__registrations")
                .order_by("sequence")
            )
            return Response(QuerySerializer(rows, many=True).data)
        probe = None
        if request.data.get("probe_public_id"):
            probe = ContentObject.objects.get(public_id=request.data["probe_public_id"])
        upload = request.FILES.get("file")
        if upload is not None:
            corpus_id = request.data.get("corpus")
            restriction = run.corpora.select_related("corpus").first()
            if corpus_id:
                corpus = Corpus.objects.filter(pk=corpus_id).first()
            elif restriction is not None:
                corpus = restriction.corpus
            else:
                corpus = Corpus.objects.filter(is_active=True).first()
            if corpus is None:
                return problem(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    type_slug="corpus-required",
                    title="Corpus required",
                    detail="An image query needs a corpus to register the probe.",
                )
            try:
                evidence = register_bytes(
                    upload.read(),
                    corpus=corpus,
                    original_filename=upload.name or "probe",
                    registered_by=request.user,
                    case=run.case,
                )
            except AlreadyRegisteredError as exc:
                evidence = exc.evidence
            probe = evidence.content
        query = submit_query(
            run=run,
            created_by=request.user,
            query_type=request.data.get("query_type", "image" if probe else "text"),
            probe_content=probe,
            query_text=request.data.get("query_text"),
        )
        return Response(QuerySerializer(query).data, status=status.HTTP_202_ACCEPTED)

    @action(detail=True, methods=["post"], permission_classes=[CanSearch])
    def save(self, request, public_id=None):
        run = self.get_object()
        save_run(run, actor=request.user)
        run = Run.objects.annotate(query_count=Count("queries")).get(pk=run.pk)
        return Response(RunSerializer(run).data)


class QueryViewSet(ScopedQuerysetMixin, viewsets.ReadOnlyModelViewSet):
    queryset = Query.objects.all().select_related("run", "probe_content")
    serializer_class = QuerySerializer
    lookup_field = "public_id"

    @action(detail=True, methods=["get"])
    def results(self, request, public_id=None):
        query = self.get_object()
        if query.status != QueryStatus.COMPLETE:
            return problem(
                status_code=status.HTTP_409_CONFLICT,
                type_slug="results-not-ready",
                title="Results not ready",
                detail="That query has not finished. Poll again shortly.",
            )
        record(
            AuditAction.RESULTS_VIEWED,
            actor=request.user,
            target=query,
            detail={"page": request.query_params.get("cursor") or "first"},
        )
        rows = (
            query.results.order_by("rank")
            .select_related("evidence_file", "evidence_file__corpus")
            .prefetch_related("approvals")
        )
        return Response(ResultSerializer(rows, many=True).data)
