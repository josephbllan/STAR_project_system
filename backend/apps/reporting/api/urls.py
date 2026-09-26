from rest_framework.routers import SimpleRouter

from apps.reporting.api.views import ReportViewSet

router = SimpleRouter()
router.register("reports", ReportViewSet)
urlpatterns = router.urls
