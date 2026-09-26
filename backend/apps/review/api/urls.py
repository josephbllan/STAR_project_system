from rest_framework.routers import SimpleRouter

from apps.review.api.views import ApprovalViewSet, NoteViewSet, RatingViewSet

router = SimpleRouter()
router.register("review/ratings", RatingViewSet, basename="rating")
router.register("review/notes", NoteViewSet, basename="note")
router.register("review/approvals", ApprovalViewSet, basename="approval")
urlpatterns = router.urls
