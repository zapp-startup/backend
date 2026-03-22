from django.urls import path
from rest_framework.routers import DefaultRouter

from .views import (
    SupabaseUserSyncView,
    UserRawExplicitViewSet,
    UserRawInferredViewSet,
    UserComputedViewSet,
    UserPreferenceViewSet,
)

router = DefaultRouter()
router.include_format_suffixes = False
router.register(r"raw-explicit", UserRawExplicitViewSet, basename="raw-explicit")
router.register(r"raw-inferred", UserRawInferredViewSet, basename="raw-inferred")
router.register(r"computed", UserComputedViewSet, basename="computed")
router.register(r"preferences", UserPreferenceViewSet, basename="preferences")

urlpatterns = [
    path("auth/sync/", SupabaseUserSyncView.as_view(), name="supabase-user-sync"),
    *router.urls,
]
