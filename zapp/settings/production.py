from .base import *
import os

DEBUG = False


def _csv_env(name: str, default: list[str]) -> list[str]:
    raw = os.getenv(name, "")
    values = [item.strip() for item in raw.split(",") if item.strip()]
    return values or default


ALLOWED_HOSTS = _csv_env(
    "ALLOWED_HOSTS",
    ["api.zappai.co", "zappai.co", "www.zappai.co"],
)
CORS_ALLOWED_ORIGINS = _csv_env(
    "CORS_ALLOWED_ORIGINS",
    ["https://zappai.co", "https://www.zappai.co"],
)
CSRF_TRUSTED_ORIGINS = _csv_env(
    "CSRF_TRUSTED_ORIGINS",
    ["https://zappai.co", "https://www.zappai.co", "https://api.zappai.co"],
)
CORS_ALLOW_CREDENTIALS = True

# Disable server-side cursors for Supabase/PgBouncer transaction pooler
DATABASES["default"]["DISABLE_SERVER_SIDE_CURSORS"] = True

# --- HTTPS / transport security (TLS terminates at load balancer; Django sees forwarded proto) ---
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_SSL_REDIRECT = True
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SECURE_HSTS_SECONDS = 31536000
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = "DENY"

# Production Plaid / banking policy
BANKING_REQUIRE_MFA = True
BANKING_REQUIRE_FINANCIAL_CONSENT = True
ALLOW_DEV_HEADER_AUTH = False

from zapp.security.production_validation import validate_production_security

validate_production_security()
