from .base import *
from .banking_env import env_bool

DEBUG = True
ALLOWED_HOSTS = ALLOWED_HOSTS or ["127.0.0.1"]

# Banking: relaxed defaults for local dev. Set in .env to mirror production when testing Plaid:
#   BANKING_REQUIRE_MFA=true
#   BANKING_REQUIRE_FINANCIAL_CONSENT=true
# Unset vars → False (dev); explicit env always wins.
BANKING_REQUIRE_MFA = env_bool("BANKING_REQUIRE_MFA", False)
BANKING_REQUIRE_FINANCIAL_CONSENT = env_bool(
    "BANKING_REQUIRE_FINANCIAL_CONSENT", False
)
BANKING_STEP_UP_REQUIRED = env_bool("BANKING_STEP_UP_REQUIRED", False)
ALLOW_DEV_HEADER_AUTH = True

CORS_ALLOWED_ORIGINS = [
    "http://127.0.0.1:3000",
    "http://127.0.0.1:4173",
    "http://127.0.0.1:5173",
]
CORS_ALLOW_CREDENTIALS = True
CSRF_TRUSTED_ORIGINS = list(CORS_ALLOWED_ORIGINS)

from corsheaders.defaults import default_headers
CORS_ALLOW_HEADERS = list(default_headers) + [
    "x-dev-user",
]

# Disable server-side cursors for Supabase/PgBouncer transaction pooler
DATABASES["default"]["DISABLE_SERVER_SIDE_CURSORS"] = True
