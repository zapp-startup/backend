"""
Test settings: in-memory SQLite (no Postgres/SSL) for CI and local `manage.py test`.
"""
from .development import *

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": ":memory:",
    }
}

PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.MD5PasswordHasher",
]
