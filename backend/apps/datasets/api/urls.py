from rest_framework.routers import SimpleRouter

from apps.datasets.api.views import CorpusViewSet, EncoderViewSet, EvidenceViewSet, MountViewSet

router = SimpleRouter()
router.register("corpora", CorpusViewSet)
router.register("encoders", EncoderViewSet)
router.register("mounts", MountViewSet)
router.register("evidence", EvidenceViewSet)

urlpatterns = router.urls
