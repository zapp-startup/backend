from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import UserProfileViewSet, UserPreferenceViewSet

router = DefaultRouter()
router.register(r"profiles", UserProfileViewSet, basename="profiles")
router.register(r"preferences", UserPreferenceViewSet, basename="preferences")

urlpatterns = [
    path("", include(router.urls)),
]
