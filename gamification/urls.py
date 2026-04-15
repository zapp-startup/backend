from rest_framework.routers import DefaultRouter
from .views import BadgeViewSet, GroupInviteViewSet, GroupViewSet, MonthlyTargetViewSet, PointEventViewSet, ReviewViewSet, UserBadgeViewSet

router = DefaultRouter()
router.include_format_suffixes = False
router.register(r"groups", GroupViewSet, basename="groups")
router.register(r"group-invites", GroupInviteViewSet, basename="group-invites")
router.register(r"monthly-targets", MonthlyTargetViewSet, basename="monthly-targets")
router.register(r"reviews", ReviewViewSet, basename="reviews")
router.register(r"points", PointEventViewSet, basename="points")
router.register(r"badges", BadgeViewSet, basename="badges")
router.register(r"user-badges", UserBadgeViewSet, basename="user-badges")

urlpatterns = router.urls
