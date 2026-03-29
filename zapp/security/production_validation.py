"""
Startup validation for production Django settings (HTTPS, cookies, HSTS).
Call from production settings after all configuration is loaded.
"""
from __future__ import annotations

import os
import sys


def validate_production_security():
    """
    Fail fast if critical security settings are misconfigured.
    Set SKIP_PRODUCTION_SECURITY_VALIDATION=1 only for emergency local prod debugging.
    """
    if os.getenv("SKIP_PRODUCTION_SECURITY_VALIDATION", "").lower() in ("1", "true", "yes"):
        return

    from django.conf import settings

    errors = []

    if not getattr(settings, "SECURE_SSL_REDIRECT", False):
        errors.append("SECURE_SSL_REDIRECT must be True in production.")

    if not getattr(settings, "SESSION_COOKIE_SECURE", False):
        errors.append("SESSION_COOKIE_SECURE must be True in production.")

    if not getattr(settings, "CSRF_COOKIE_SECURE", False):
        errors.append("CSRF_COOKIE_SECURE must be True in production.")

    sec = getattr(settings, "SECURE_HSTS_SECONDS", 0) or 0
    if sec < 60:
        errors.append("SECURE_HSTS_SECONDS should be set (e.g. 31536000) in production.")

    if not getattr(settings, "SECURE_PROXY_SSL_HEADER", None):
        errors.append(
            "SECURE_PROXY_SSL_HEADER should be set when TLS terminates at a load balancer "
            "(e.g. ('HTTP_X_FORWARDED_PROTO', 'https'))."
        )

    if errors:
        msg = "Production security validation failed:\n" + "\n".join(f" - {e}" for e in errors)
        print(msg, file=sys.stderr)
        raise RuntimeError(msg)
