"""Dedicated throttles for credential endpoints (anti brute-force)."""
from __future__ import annotations

from rest_framework.throttling import AnonRateThrottle


class LoginRateThrottle(AnonRateThrottle):
    """
    Tight, dedicated limit on the password-login endpoint.

    Keyed on (client identity, email) so a single source cannot brute-force a
    specific account's password, while a legitimate user is not throttled by
    attempts other sources make against the same email. TLS terminates at a
    load balancer and no NUM_PROXIES is configured, so ``get_ident`` may resolve
    to the proxy address; the email component keeps the key meaningful in that
    case. Supabase's own auth rate-limiting is the second layer against
    distributed (IP-rotating) attacks.
    """

    scope = "auth_login"

    def get_cache_key(self, request, view):
        ident = self.get_ident(request)
        email = ""
        data = getattr(request, "data", None)
        if isinstance(data, dict):
            email = (data.get("email") or "").strip().lower()
        return self.cache_format % {
            "scope": self.scope,
            "ident": f"{ident}:{email}" if email else ident,
        }


class SignupRateThrottle(AnonRateThrottle):
    """Per-source cap on account creation to limit signup abuse."""

    scope = "auth_signup"
