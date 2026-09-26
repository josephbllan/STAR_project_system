from __future__ import annotations

from rest_framework import serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.accounts.permissions import IsAdministrator, IsInvestigator
from apps.audit.models import AuditAction
from apps.audit.recorder import record
from apps.common import storage
from apps.common.permissions import ScopedQuerysetMixin
from apps.common.storage import InvalidLocalFolderError
from django.db.models import Count, Q

from apps.datasets.models import (
    ArtifactKind,
    Corpus,
    DerivedArtifact,
    DigestVerification,
    EvidenceFile,
    EvidenceState,
    Mount,
)
from apps.datasets.services import (
    AlreadyRegisteredError,
    register_bytes,
    request_corpus_encoding,
)
from apps.search.models import Encoder
from apps.tasks.dispatch import dispatch


class CorpusSerializer(serializers.ModelSerializer):
    evidence_count = serializers.SerializerMethodField()
    indexed_count = serializers.SerializerMethodField()

    class Meta:
        model = Corpus
        fields = (
            "id",
            "code",
            "name",
            "description",
            "data_classification",
            "is_active",
            "evidence_count",
            "indexed_count",
        )

    def get_evidence_count(self, obj: Corpus) -> int:
        annotated = getattr(obj, "evidence_count", None)
        if isinstance(annotated, int) and annotated > 0:
            return annotated
        return obj.evidence_files.count()

    def get_indexed_count(self, obj: Corpus) -> int:
        annotated = getattr(obj, "indexed_count", None)
        if isinstance(annotated, int) and annotated > 0:
            return annotated
        return obj.evidence_files.filter(state=EvidenceState.INDEXED).count()


class EncoderSerializer(serializers.ModelSerializer):
    class Meta:
        model = Encoder
        fields = (
            "id",
            "name",
            "family",
            "version",
            "preprocess_version",
            "dimensions",
            "is_active",
        )


class MountSerializer(serializers.ModelSerializer):
    corpus_code = serializers.CharField(source="corpus.code", read_only=True)

    class Meta:
        model = Mount
        fields = (
            "id",
            "corpus",
            "corpus_code",
            "path",
            "label",
            "is_enabled",
            "created_at",
            "last_scan_at",
            "last_scan_status",
            "last_scan_files_seen",
            "last_scan_error_count",
        )
        extra_kwargs = {
            "created_at": {"read_only": True},
            "last_scan_at": {"read_only": True},
            "last_scan_status": {"read_only": True},
            "last_scan_files_seen": {"read_only": True},
            "last_scan_error_count": {"read_only": True},
        }

    def validate_path(self, value: str) -> str:
        try:
            return str(storage.resolve_local_folder(value))
        except InvalidLocalFolderError as exc:
            raise serializers.ValidationError(str(exc)) from exc

    def validate_corpus(self, corpus):
        if not corpus.is_active:
            raise serializers.ValidationError("That corpus is not active")
        return corpus


class EvidenceSerializer(serializers.ModelSerializer):
    corpus_code = serializers.CharField(source="corpus.code", read_only=True)
    thumbnail_access = serializers.SerializerMethodField()

    class Meta:
        model = EvidenceFile
        fields = (
            "public_id",
            "corpus",
            "corpus_code",
            "case",
            "original_filename",
            "source_path",
            "state",
            "registered_at",
            "thumbnail_access",
        )

    def get_thumbnail_access(self, obj: EvidenceFile) -> str:
        return f"/api/v1/evidence/{obj.public_id}/access-url/"


class CorpusViewSet(viewsets.ModelViewSet):
    queryset = Corpus.objects.all()
    serializer_class = CorpusSerializer
    http_method_names = ["get", "post", "patch", "head", "options"]

    def get_queryset(self):
        return Corpus.objects.annotate(
            evidence_count=Count("evidence_files", distinct=True),
            indexed_count=Count(
                "evidence_files",
                filter=Q(evidence_files__state=EvidenceState.INDEXED),
                distinct=True,
            ),
        ).order_by("code")

    def get_permissions(self):
        if self.action in {"create", "partial_update"}:
            return [IsAdministrator()]
        if self.action in {"encode", "coverage"}:
            return [IsInvestigator()] if self.action == "encode" else [IsAuthenticated()]
        return [IsAuthenticated()]

    @action(detail=True, methods=["post"], permission_classes=[IsInvestigator])
    def encode(self, request, pk=None):
        corpus = self.get_object()
        pairs = request_corpus_encoding(corpus, requested_by=request.user)
        return Response(
            {
                "runs": [
                    {"public_id": str(run.public_id), "created": created} for run, created in pairs
                ],
                "already_in_progress": bool(pairs) and all(not created for _, created in pairs),
            },
            status=status.HTTP_202_ACCEPTED,
        )

    @action(detail=False, methods=["get"])
    def coverage(self, request):
        serializer = self.get_serializer(self.get_queryset(), many=True)
        return Response({"results": serializer.data})


class EncoderViewSet(viewsets.ModelViewSet):
    queryset = Encoder.objects.all().order_by("name")
    serializer_class = EncoderSerializer
    http_method_names = ["get", "post", "head", "options"]

    def get_permissions(self):
        if self.action == "create":
            return [IsAdministrator()]
        return [IsAuthenticated()]

    @action(detail=False, methods=["get"])
    def recall(self, request):  # noqa: ARG002
        from apps.search.models import AnnIndexBuild

        rows = []
        for encoder in self.get_queryset().filter(is_active=True):
            build = (
                AnnIndexBuild.objects.filter(encoder=encoder).order_by("-built_at", "-id").first()
            )
            rows.append(
                {
                    "encoder_id": encoder.pk,
                    "encoder_name": encoder.name,
                    "family": encoder.family,
                    "measured_recall": (
                        str(build.measured_recall)
                        if build is not None and build.measured_recall is not None
                        else None
                    ),
                    "recall_k": build.recall_k if build is not None else None,
                    "measured_at": build.built_at.isoformat() if build is not None else None,
                    "m": build.m if build is not None else None,
                    "ef_construction": build.ef_construction if build is not None else None,
                    "ef_search": build.ef_search if build is not None else None,
                }
            )
        return Response({"results": rows})


class MountViewSet(viewsets.ModelViewSet):
    queryset = Mount.objects.select_related("corpus").order_by("path")
    serializer_class = MountSerializer
    http_method_names = ["get", "post", "patch", "head", "options"]

    def get_permissions(self):
        if self.action in {"create", "scan"}:
            return [IsInvestigator()]
        if self.action == "partial_update":
            return [IsAdministrator()]
        return [IsAuthenticated()]

    def perform_create(self, serializer):
        mount = serializer.save()
        dispatch(
            "datasets.scan_mount",
            {"mount_id": mount.pk, "target_public_id": str(mount.pk)},
            scope_token=f"mount:{mount.pk}:{timezone_now()}",
            requested_by=self.request.user,
            target_type="mount",
        )

    @action(detail=True, methods=["post"])
    def scan(self, request, pk=None):
        mount = self.get_object()
        run, created = dispatch(
            "datasets.scan_mount",
            {"mount_id": mount.pk, "target_public_id": str(mount.pk)},
            scope_token=f"mount:{mount.pk}:{timezone_now()}",
            requested_by=request.user,
            target_type="mount",
        )
        return Response({"public_id": str(run.public_id)}, status=202 if created else 200)


class EvidenceViewSet(ScopedQuerysetMixin, viewsets.ReadOnlyModelViewSet):
    queryset = EvidenceFile.objects.all().select_related("content", "corpus", "case")
    serializer_class = EvidenceSerializer
    permission_classes = [IsAuthenticated]
    lookup_field = "public_id"

    def get_queryset(self):
        qs = super().get_queryset()
        corpus = self.request.query_params.get("corpus")
        if corpus:
            qs = qs.filter(corpus_id=corpus)
        mount = self.request.query_params.get("mount")
        if mount:
            qs = qs.filter(mount_id=mount)
        state = self.request.query_params.get("state")
        if state:
            qs = qs.filter(state=state)
        return qs

    @action(detail=False, methods=["post"], url_path="uploads", permission_classes=[IsInvestigator])
    def uploads(self, request):
        upload = request.FILES.get("file")
        if upload is None:
            return Response({"detail": "file is required"}, status=400)
        corpus = Corpus.objects.get(pk=request.data.get("corpus"))
        data = upload.read()
        try:
            evidence = register_bytes(
                data,
                corpus=corpus,
                original_filename=upload.name or "",
                registered_by=request.user,
            )
            return Response(EvidenceSerializer(evidence).data, status=status.HTTP_201_CREATED)
        except AlreadyRegisteredError as exc:
            return Response(EvidenceSerializer(exc.evidence).data, status=status.HTTP_200_OK)

    @action(detail=True, methods=["post"], url_path="access-url")
    def access_url(self, request, public_id=None):
        evidence = self.get_object()
        variant = str(request.data.get("variant") or "original")
        key = evidence.content.storage_key
        if variant == "thumbnail":
            artifact = (
                DerivedArtifact.objects.filter(source_file=evidence, kind=ArtifactKind.THUMBNAIL)
                .order_by("-id")
                .first()
            )
            if artifact is not None:
                key = artifact.storage_key
        grant = storage.signed_url(key)
        event = record(AuditAction.EVIDENCE_URL_ISSUED, actor=request.user, target=evidence)
        return Response(
            {
                "url": grant.url,
                "expires_at": grant.expires_at,
                "variant": variant,
                "audit_event": str(event.public_id) if event is not None else None,
            }
        )

    @action(detail=True, methods=["get"])
    def artifacts(self, request, public_id=None):
        evidence = self.get_object()
        items = DerivedArtifact.objects.filter(source_file=evidence).values(
            "public_id", "kind", "operation", "byte_size"
        )
        return Response(list(items))

    @action(detail=True, methods=["get"])
    def verifications(self, request, public_id=None):
        evidence = self.get_object()
        items = DigestVerification.objects.filter(content=evidence.content).values(
            "outcome", "verified_at", "expected_sha256"
        )
        return Response(list(items))


def timezone_now():
    from django.utils import timezone

    return timezone.now().isoformat()
