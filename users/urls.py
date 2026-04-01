from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .auth_views import (
    AssuranceView,
    CsrfView,
    LoginView,
    LogoutView,
    MeView,
    MfaChallengeView,
    MfaVerifyView,
    OAuthCallbackView,
    OAuthStartView,
    SignupView,
)
from .security_views import AuthAssuranceView
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
    path("auth/csrf/", CsrfView.as_view(), name="auth-csrf"),
    path("auth/login/", LoginView.as_view(), name="auth-login"),
    path("auth/signup/", SignupView.as_view(), name="auth-signup"),
    path("auth/logout/", LogoutView.as_view(), name="auth-logout"),
    path("auth/me/", MeView.as_view(), name="auth-me"),
    path("auth/oauth/start/", OAuthStartView.as_view(), name="auth-oauth-start"),
    path("auth/oauth/callback/", OAuthCallbackView.as_view(), name="auth-oauth-callback"),
    path("auth/mfa/challenge/", MfaChallengeView.as_view(), name="auth-mfa-challenge"),
    path("auth/mfa/verify/", MfaVerifyView.as_view(), name="auth-mfa-verify"),
    path("auth/assurance/", AssuranceView.as_view(), name="auth-assurance"),
    path("auth/sync/", SupabaseUserSyncView.as_view(), name="supabase-user-sync"),
    path("security/auth-assurance/", AuthAssuranceView.as_view(), name="auth-assurance-legacy"),
    path("", include(router.urls)),
]
