from django.conf import settings
from rest_framework import status
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.viewsets import ModelViewSet, ReadOnlyModelViewSet

from apps.gamification.services import award_points_for_onboarding

from .session_auth import (
    get_session_auth_state,
    issue_pending_mfa_session,
    session_requires_pending_mfa,
)
from .session_authentication import AuthSessionAuthentication
from .models import UserRawExplicit, UserRawInferred, UserComputed, UserPreference
from .services.state_computed import recompute_user_computed
from .services.state_orchestrator import mark_user_state_dirty, recompute_user_state
from .serializers import (
    UserRawExplicitSerializer,
    UserRawInferredSerializer,
    UserComputedSerializer,
    UserPreferenceSerializer,
)
from .supabase_auth import SupabaseJWTAuthentication


def _has_completed_onboarding(user) -> bool:
    if not getattr(user, "pk", None):
        return False
    return UserRawExplicit.objects.filter(user_id=user.pk).exists()


def _resolve_next_steps(
    *,
    mfa_pending: bool,
    mfa_enrollment_required: bool,
    onboarding_completed: bool,
) -> tuple[str, str | None]:
    post_login_step = "dashboard" if onboarding_completed else "onboarding_survey"
    if mfa_pending:
        if mfa_enrollment_required:
            return "mfa_setup", post_login_step
        return "mfa_verify", post_login_step
    return post_login_step, None


class UserRawExplicitViewSet(ModelViewSet):
    serializer_class = UserRawExplicitSerializer
    authentication_classes = [AuthSessionAuthentication, SupabaseJWTAuthentication]
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return UserRawExplicit.objects.filter(user=self.request.user)

    def perform_create(self, serializer):
        profile = serializer.save(user=self.request.user)
        award_points_for_onboarding(profile.user)
        recompute_user_computed(self.request.user.id)
        mark_user_state_dirty(self.request.user.id, reason="raw_explicit_created", priority=2)

    def perform_update(self, serializer):
        serializer.save(user=self.request.user)
        recompute_user_computed(self.request.user.id)
        mark_user_state_dirty(self.request.user.id, reason="raw_explicit_updated", priority=2)


class UserRawInferredViewSet(ReadOnlyModelViewSet):
    serializer_class = UserRawInferredSerializer
    authentication_classes = [AuthSessionAuthentication, SupabaseJWTAuthentication]
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return UserRawInferred.objects.filter(user=self.request.user)


class UserComputedViewSet(ReadOnlyModelViewSet):
    serializer_class = UserComputedSerializer
    authentication_classes = [AuthSessionAuthentication, SupabaseJWTAuthentication]
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return UserComputed.objects.filter(user=self.request.user)


class UserPreferenceViewSet(ModelViewSet):
    serializer_class = UserPreferenceSerializer
    authentication_classes = [AuthSessionAuthentication, SupabaseJWTAuthentication]
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return UserPreference.objects.filter(user=self.request.user)

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)

    def get_object(self):
        obj = super().get_object()
        if obj.user_id != self.request.user.id:
            raise PermissionDenied("You can only access your own preferences.")
        return obj


class SupabaseUserSyncView(APIView):
    """
    Sync the authenticated Supabase session into a backend user profile payload.
    """

    authentication_classes = [AuthSessionAuthentication, SupabaseJWTAuthentication]
    permission_classes = [IsAuthenticated]

    def post(self, request):
        user = request.user
        onboarding_completed = _has_completed_onboarding(user)
        auth_context = request.auth if isinstance(request.auth, dict) else {}
        if not auth_context:
            state = get_session_auth_state(request) or {}
            if isinstance(state, dict):
                auth_context = {
                    "aal": state.get("aal"),
                    "mfa_factors_count": state.get("mfa_factor_count", -1),
                    "mfa_pending": bool(state.get("mfa_pending")),
                    "mfa_enrollment_required": bool(state.get("mfa_enrollment_required")),
                    "_assurance_source": "session",
                }

        if getattr(settings, "AUTH_REQUIRE_AAL2", False) and auth_context:
            pending_mfa = bool(auth_context.get("mfa_pending"))
            enrollment_required = bool(auth_context.get("mfa_enrollment_required"))
            factor_count = auth_context.get("mfa_factors_count", -1)
            try:
                factor_count_int = int(factor_count)
            except (TypeError, ValueError):
                factor_count_int = -1
            if not pending_mfa:
                pending_mfa, enrollment_required = session_requires_pending_mfa(
                    auth_context.get("aal"),
                    factor_count,
                )
            elif factor_count_int == 0:
                enrollment_required = True
            elif factor_count_int > 0:
                enrollment_required = False
            if pending_mfa:
                auth_source = str(auth_context.get("_assurance_source") or "jwt")
                header = request.headers.get("Authorization") or ""
                if header.startswith("Bearer "):
                    access_token = header.split(" ", 1)[1].strip()
                    if access_token:
                        issue_pending_mfa_session(
                            request,
                            user,
                            {
                                "aal": auth_context.get("aal"),
                                "auth_method": "supabase_jwt",
                                "mfa_factor_count": auth_context.get("mfa_factors_count", -1),
                            },
                            access_token,
                            None,
                            None,
                            mfa_enrollment_required=enrollment_required,
                        )
                next_step, post_mfa_step = _resolve_next_steps(
                    mfa_pending=True,
                    mfa_enrollment_required=enrollment_required,
                    onboarding_completed=onboarding_completed,
                )
                return Response(
                    {
                        "detail": (
                            "Set up MFA to finish signing in."
                            if enrollment_required
                            else "Enter your authenticator code to finish signing in."
                        ),
                        "error_code": (
                            "mfa_enrollment_required"
                            if enrollment_required
                            else "mfa_required"
                        ),
                        "next_aal": "aal2",
                        "mfa_pending": True,
                        "mfa_enrollment_required": enrollment_required,
                        "auth_source": auth_source,
                        "onboarding_completed": onboarding_completed,
                        "onboarding_required": not onboarding_completed,
                        "next_step": next_step,
                        "post_mfa_step": post_mfa_step,
                        "requires_action": True,
                    },
                    status=status.HTTP_200_OK,
                )

        next_step, post_mfa_step = _resolve_next_steps(
            mfa_pending=False,
            mfa_enrollment_required=False,
            onboarding_completed=onboarding_completed,
        )
        return Response(
            {
                "id": user.id,
                "email": user.email,
                "username": user.username,
                "supabase_uid": str(user.supabase_uid) if user.supabase_uid else None,
                "mfa_pending": False,
                "mfa_enrollment_required": False,
                "onboarding_completed": onboarding_completed,
                "onboarding_required": not onboarding_completed,
                "next_step": next_step,
                "post_mfa_step": post_mfa_step,
            }
        )


class UserStateView(APIView):
    authentication_classes = [AuthSessionAuthentication, SupabaseJWTAuthentication]
    permission_classes = [IsAuthenticated]

    def get(self, request):
        recompute = request.query_params.get("recompute")
        if recompute == "true":
            recompute_user_state(request.user.id)
        explicit = UserRawExplicit.objects.filter(user=request.user).first()
        inferred = UserRawInferred.objects.filter(user=request.user).first()
        computed = UserComputed.objects.filter(user=request.user).first()
        return Response(
            {
                "explicit": UserRawExplicitSerializer(explicit).data if explicit else None,
                "inferred": UserRawInferredSerializer(inferred).data if inferred else None,
                "computed": UserComputedSerializer(computed).data if computed else None,
            }
        )
