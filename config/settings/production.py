from .base import *

DEBUG = False


def _csv_env(value: str | None) -> list[str]:
    if not value:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]

if not ALLOWED_HOSTS:
    ALLOWED_HOSTS = ["api.zappai.co", "zappai.co", "www.zappai.co"]

CORS_ALLOWED_ORIGINS = _csv_env(os.getenv("CORS_ALLOWED_ORIGINS")) or [
    "https://zappai.co",
    "https://www.zappai.co",
]
CSRF_TRUSTED_ORIGINS = _csv_env(os.getenv("CSRF_TRUSTED_ORIGINS")) or [
    "https://zappai.co",
    "https://www.zappai.co",
    "https://api.zappai.co",
]
CORS_ALLOW_CREDENTIALS = True

# Disable server-side cursors for Supabase/PgBouncer transaction pooler
DATABASES["default"]["DISABLE_SERVER_SIDE_CURSORS"] = True

# --- HTTPS / transport security (TLS terminates at load balancer; Django sees forwarded proto) ---
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_SSL_REDIRECT = True
SESSION_COOKIE_SECURE = True
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SECURE = True
CSRF_COOKIE_SAMESITE = "Lax"
SECURE_HSTS_SECONDS = 31536000
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = "DENY"

# Production Plaid / banking policy
AUTH_REQUIRE_AAL2 = True
BANKING_REQUIRE_MFA = True
BANKING_REQUIRE_FINANCIAL_CONSENT = True
ALLOW_DEV_HEADER_AUTH = False

from config.security.production_validation import validate_production_security

validate_production_security()
