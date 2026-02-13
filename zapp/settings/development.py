from .base import *

DEBUG = True
ALLOWED_HOSTS = ["127.0.0.1", "localhost"]

CORS_ALLOWED_ORIGINS = [
    "http://localhost:5173",
    "http://127.0.0.1:5173",
]
#dev auth header should be removed later ??
from corsheaders.defaults import default_headers

CORS_ALLOW_HEADERS = list(default_headers) + [
    "x-dev-user",
]