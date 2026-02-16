from django.urls import include, path
from rest_framework.routers import DefaultRouter
from .views import GroupViewSet, PointEventViewSet

router = DefaultRouter()
router.register(r"groups", GroupViewSet, basename="groups")
router.register(r"points", PointEventViewSet, basename="points")

urlpatterns = [
    path("", include(router.urls)),
]
