from django.test import SimpleTestCase, override_settings

from zapp.security.production_validation import validate_production_security


class ProductionValidationTests(SimpleTestCase):
    @override_settings(
        ALLOWED_HOSTS=["api.example.com"],
        SECURE_SSL_REDIRECT=True,
        SESSION_COOKIE_SECURE=True,
        CSRF_COOKIE_SECURE=True,
        SECURE_HSTS_SECONDS=31536000,
        SECURE_PROXY_SSL_HEADER=("HTTP_X_FORWARDED_PROTO", "https"),
        PLAID_TOKEN_ENCRYPTION_KEY="8bUpWwzYgUN7ctklDvqGELWMKhfYbsxxNaKzUknYI5Q=",
    )
    def test_validate_passes_when_configured(self):
        validate_production_security()
