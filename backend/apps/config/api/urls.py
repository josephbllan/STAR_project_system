from rest_framework.routers import SimpleRouter

from apps.config.api.views import SettingViewSet

router = SimpleRouter()
router.register("settings", SettingViewSet)
urlpatterns = router.urls
