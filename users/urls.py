from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import AuthSessionView, UserProfileViewSet, UserPreferenceViewSet

router = DefaultRouter()
router.register(r"profiles", UserProfileViewSet, basename="profiles")
router.register(r"preferences", UserPreferenceViewSet, basename="preferences")

urlpatterns = [
    path("", include(router.urls)),
    path("auth/session/", AuthSessionView.as_view(), name="auth-session"),
]
