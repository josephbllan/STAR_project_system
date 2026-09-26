from rest_framework.routers import SimpleRouter

from apps.audit.api.views import AuditEventViewSet

router = SimpleRouter()
router.register("audit", AuditEventViewSet)
urlpatterns = router.urls
