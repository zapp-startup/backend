from __future__ import annotations

from django.contrib.auth import get_user_model
from rest_framework.authentication import SessionAuthentication

from .session_auth import get_session_auth_state

User = get_user_model()


class AuthSessionAuthentication(SessionAuthentication):
    """
    Session authentication that also supports pending-MFA auth blocks.

    Full Django-authenticated sessions behave like normal SessionAuthentication.
    When the Django auth session has not been finalized yet, selected views can
    still resolve the user from the server-side AUTH_SESSION_KEY block while
    keeping DRF's CSRF enforcement for unsafe methods.
    """

    def authenticate(self, request):
        session_result = super().authenticate(request)
        if session_result is not None:
            return session_result

        state = get_session_auth_state(request)
        if not state:
            return None

        user_id = state.get("user_id")
        if not user_id:
            return None

        user = User.objects.filter(pk=user_id).first()
        if user is None:
            return None

        self.enforce_csrf(request)
        auth_context = {
            "aal": state.get("aal"),
            "mfa_factors_count": state.get("mfa_factor_count", -1),
            "last_step_up_at": state.get("last_step_up_at"),
            "mfa_pending": bool(state.get("mfa_pending")),
            "next_aal": state.get("next_aal"),
            "mfa_enrollment_required": bool(state.get("mfa_enrollment_required")),
            "_assurance_source": "session",
        }
        return (user, auth_context)
