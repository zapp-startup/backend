"""
Test settings: in-memory SQLite (no Postgres/SSL) for CI and local `manage.py test`.
"""
from .development import *

DEBUG = False
ALLOWED_HOSTS = ["testserver", "127.0.0.1"]

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": ":memory:",
    }
}

OPENAI_API_KEY = None

PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.MD5PasswordHasher",
]
