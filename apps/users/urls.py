from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .auth_views import (
    AssuranceView,
    CsrfView,
    EmailConfirmCallbackView,
    LoginView,
    LogoutView,
    MeView,
    MfaChallengeView,
    MfaEnrollView,
    MfaFactorView,
    MfaSnapshotView,
    MfaVerifyView,
    MfaVerifyEnrollmentView,
    OAuthCallbackView,
    OAuthStartView,
    SignupView,
)
from .security_views import AuthAssuranceView
from .views import (
    SupabaseUserSyncView,
    UserStateView,
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
    path("auth/email/confirm/", EmailConfirmCallbackView.as_view(), name="auth-email-confirm-callback"),
    path("auth/mfa/snapshot/", MfaSnapshotView.as_view(), name="auth-mfa-snapshot"),
    path("auth/mfa/enroll/", MfaEnrollView.as_view(), name="auth-mfa-enroll"),
    path("auth/mfa/verify-enrollment/", MfaVerifyEnrollmentView.as_view(), name="auth-mfa-verify-enrollment"),
    path("auth/mfa/challenge/", MfaChallengeView.as_view(), name="auth-mfa-challenge"),
    path("auth/mfa/verify/", MfaVerifyView.as_view(), name="auth-mfa-verify"),
    path("auth/mfa/factors/<str:factor_id>/", MfaFactorView.as_view(), name="auth-mfa-factor"),
    path("auth/assurance/", AssuranceView.as_view(), name="auth-assurance"),
    path("auth/sync/", SupabaseUserSyncView.as_view(), name="supabase-user-sync"),
    path("security/auth-assurance/", AuthAssuranceView.as_view(), name="auth-assurance-legacy"),
    path("user-state/", UserStateView.as_view(), name="user-state"),
    path("", include(router.urls)),
]
