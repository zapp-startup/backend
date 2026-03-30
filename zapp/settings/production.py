from .base import *

DEBUG = False
ALLOWED_HOSTS = ["your-production-domain.com"]  # update later

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
