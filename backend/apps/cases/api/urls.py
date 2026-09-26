from rest_framework.routers import SimpleRouter

from apps.cases.api.views import CaseViewSet, QueryViewSet, RunViewSet

router = SimpleRouter()
router.register("cases", CaseViewSet)
router.register("runs", RunViewSet)
router.register("queries", QueryViewSet)
urlpatterns = router.urls
