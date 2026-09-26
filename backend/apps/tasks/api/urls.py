from rest_framework.routers import SimpleRouter

from apps.tasks.api.views import TaskRunViewSet

router = SimpleRouter()
router.register("tasks", TaskRunViewSet, basename="taskrun")
urlpatterns = router.urls
