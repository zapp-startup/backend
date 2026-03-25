from .base import *

DEBUG = False
ALLOWED_HOSTS = ["your-production-domain.com"]  # update later

# Disable server-side cursors for Supabase/PgBouncer transaction pooler
DATABASES["default"]["DISABLE_SERVER_SIDE_CURSORS"] = True