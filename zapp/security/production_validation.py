"""
Startup validation for production Django settings (HTTPS, cookies, HSTS).
Call from production settings after all configuration is loaded.
"""
from __future__ import annotations

import os
import sys
from urllib.parse import urlparse

from cryptography.fernet import Fernet


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

    allowed_hosts = [str(host).strip() for host in getattr(settings, "ALLOWED_HOSTS", []) if str(host).strip()]
    if not allowed_hosts:
        errors.append("ALLOWED_HOSTS must be set in production.")
    if any(host in {"localhost", "127.0.0.1", "your-production-domain.com"} for host in allowed_hosts):
        errors.append("ALLOWED_HOSTS must not contain localhost or placeholder hostnames in production.")

    for setting_name in ("PLAID_WEBHOOK_URL", "PLAID_REDIRECT_URI"):
        value = (getattr(settings, setting_name, "") or "").strip()
        if not value:
            continue
        parsed = urlparse(value)
        if parsed.scheme.lower() != "https":
            errors.append(f"{setting_name} must use https:// in production when set.")
        if parsed.hostname in {"localhost", "127.0.0.1"}:
            errors.append(f"{setting_name} must not point to localhost in production.")

    plaid_token_key = (getattr(settings, "PLAID_TOKEN_ENCRYPTION_KEY", "") or "").strip()
    if not plaid_token_key:
        errors.append("PLAID_TOKEN_ENCRYPTION_KEY must be set in production.")
    else:
        try:
            Fernet(plaid_token_key.encode("ascii"))
        except Exception:
            errors.append("PLAID_TOKEN_ENCRYPTION_KEY must be a valid Fernet key in production.")

    if errors:
        msg = "Production security validation failed:\n" + "\n".join(f" - {e}" for e in errors)
        print(msg, file=sys.stderr)
        raise RuntimeError(msg)
