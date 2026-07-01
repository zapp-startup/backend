"""Shared DRF permissions for session-backed, MFA-aware authorization."""
from __future__ import annotations

from rest_framework.permissions import BasePermission

from .session_auth import get_session_auth_state


class IsFullyAuthenticated(BasePermission):
    """
    Allow only sessions that have cleared MFA step-up (AAL2 when required).

    A pending-MFA session is authenticated enough to drive the MFA step-up flow
    (via ``AuthSessionAuthentication``), but must not reach financial or profile
    data. The project default of plain ``SessionAuthentication`` already refuses
    to resolve a pending-MFA session on most endpoints; this permission is the
    explicit, defense-in-depth guard for any view that opts into
    ``AuthSessionAuthentication`` and therefore *can* see a half-authenticated
    session.

    Sessions with no server-side auth block (e.g. the dev ``X-Dev-User`` header
    or DRF ``force_authenticate`` in tests) are treated as fully authenticated,
    since there is no pending-MFA state to honor.
    """

    message = "Complete multi-factor authentication to continue."

    def has_permission(self, request, view) -> bool:
        user = getattr(request, "user", None)
        if user is None or not user.is_authenticated:
            return False
        state = get_session_auth_state(request) or {}
        return not bool(state.get("mfa_pending", False))
