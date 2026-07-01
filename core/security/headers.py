"""
Security response headers for the API.

Django's SecurityMiddleware already emits Referrer-Policy, Cross-Origin-Opener
-Policy and (in production) X-Content-Type-Options. The notable gaps it does
not cover are Content-Security-Policy and Permissions-Policy, which this
middleware adds.

The CSP value comes from ``settings.CONTENT_SECURITY_POLICY``. When
``settings.CONTENT_SECURITY_POLICY_REPORT_ONLY`` is true it is emitted as a
report-only header (observed but not enforced) so a policy can be rolled out
and tuned before enforcement. This is a JSON API, so the strict default policy
(``default-src 'none'``) does not affect API consumption; the browser SPA is
served separately and carries its own CSP.
"""
from __future__ import annotations

from django.conf import settings


class SecurityHeadersMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)

        csp = getattr(settings, "CONTENT_SECURITY_POLICY", None)
        if csp:
            header_name = (
                "Content-Security-Policy-Report-Only"
                if getattr(settings, "CONTENT_SECURITY_POLICY_REPORT_ONLY", False)
                else "Content-Security-Policy"
            )
            response.setdefault(header_name, csp)

        permissions_policy = getattr(
            settings,
            "PERMISSIONS_POLICY",
            "geolocation=(), microphone=(), camera=(), payment=()",
        )
        if permissions_policy:
            response.setdefault("Permissions-Policy", permissions_policy)

        # Defensive: ensure these are present even outside production, where
        # SecurityMiddleware's settings-driven equivalents may be disabled.
        response.setdefault("X-Content-Type-Options", "nosniff")
        response.setdefault("Referrer-Policy", "same-origin")
        return response
