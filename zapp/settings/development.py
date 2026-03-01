from .base import *

DEBUG = True
ALLOWED_HOSTS = ["127.0.0.1", "localhost"]

CORS_ALLOWED_ORIGINS = [
    "http://localhost:5173",
    "http://127.0.0.1:5173",
]

from corsheaders.defaults import default_headers
CORS_ALLOW_HEADERS = list(default_headers) + [
    "x-dev-user",
]

# Disable server-side cursors for Supabase/PgBouncer transaction pooler
DATABASES["default"]["DISABLE_SERVER_SIDE_CURSORS"] = True