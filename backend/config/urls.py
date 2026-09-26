from django.contrib import admin
from django.http import HttpRequest, JsonResponse
from django.urls import include, path
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.common.views import stored_object


def healthz(_request: HttpRequest) -> JsonResponse:
    """Liveness only. It touches no dependency, so a slow database cannot restart the process."""
    return JsonResponse({"status": "ok"})


class VersionView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request) -> Response:
        return Response({"name": "shoerag-web", "version": "1.0.0"})


schema_view = SpectacularAPIView.as_view()
docs_view = SpectacularSwaggerView.as_view(url_name="schema")
version_view = VersionView.as_view()

urlpatterns = [
    path("healthz", healthz),
    path("admin/", admin.site.urls),
    path("api/v1/schema/", schema_view, name="schema"),
    path("api/v1/docs/", docs_view, name="docs"),
    path("api/v1/version/", version_view, name="version"),
    path("api/v1/", include("apps.accounts.api.urls")),
    path("api/v1/", include("apps.datasets.api.urls")),
    path("api/v1/", include("apps.cases.api.urls")),
    path("api/v1/", include("apps.review.api.urls")),
    path("api/v1/", include("apps.reporting.api.urls")),
    path("api/v1/", include("apps.tasks.api.urls")),
    path("api/v1/", include("apps.audit.api.urls")),
    path("api/v1/", include("apps.config.api.urls")),
    path("files/<str:token>", stored_object, name="stored-object"),
]
