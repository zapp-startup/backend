from django.test import SimpleTestCase, override_settings

from zapp.security.production_validation import validate_production_security


class ProductionValidationTests(SimpleTestCase):
    @override_settings(
        ALLOWED_HOSTS=["api.example.com"],
        SECURE_SSL_REDIRECT=True,
        SESSION_COOKIE_SECURE=True,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        CSRF_COOKIE_SECURE=True,
        CSRF_COOKIE_SAMESITE="Lax",
        SECURE_HSTS_SECONDS=31536000,
        SECURE_PROXY_SSL_HEADER=("HTTP_X_FORWARDED_PROTO", "https"),
        PLAID_WEBHOOK_URL="https://api.example.com/plaid/webhook",
        PLAID_REDIRECT_URI="https://app.example.com/auth/plaid/callback",
        PLAID_TOKEN_ENCRYPTION_KEY="8bUpWwzYgUN7ctklDvqGELWMKhfYbsxxNaKzUknYI5Q=",
    )
    def test_validate_passes_when_configured(self):
        validate_production_security()

    @override_settings(
        ALLOWED_HOSTS=["api.example.com"],
        SECURE_SSL_REDIRECT=True,
        SESSION_COOKIE_SECURE=True,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        CSRF_COOKIE_SECURE=True,
        CSRF_COOKIE_SAMESITE="Lax",
        SECURE_HSTS_SECONDS=31536000,
        SECURE_PROXY_SSL_HEADER=("HTTP_X_FORWARDED_PROTO", "https"),
        PLAID_WEBHOOK_URL="http://api.example.com/plaid/webhook",
        PLAID_TOKEN_ENCRYPTION_KEY="8bUpWwzYgUN7ctklDvqGELWMKhfYbsxxNaKzUknYI5Q=",
    )
    def test_validate_fails_when_plaid_webhook_is_not_https(self):
        with self.assertRaises(RuntimeError):
            validate_production_security()

    @override_settings(
        ALLOWED_HOSTS=["api.example.com"],
        SECURE_SSL_REDIRECT=True,
        SESSION_COOKIE_SECURE=True,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        CSRF_COOKIE_SECURE=True,
        CSRF_COOKIE_SAMESITE="Lax",
        SECURE_HSTS_SECONDS=31536000,
        SECURE_PROXY_SSL_HEADER=("HTTP_X_FORWARDED_PROTO", "https"),
        PLAID_REDIRECT_URI="https://localhost:3000/plaid/callback",
        PLAID_TOKEN_ENCRYPTION_KEY="8bUpWwzYgUN7ctklDvqGELWMKhfYbsxxNaKzUknYI5Q=",
    )
    def test_validate_fails_when_plaid_redirect_uri_points_to_localhost(self):
        with self.assertRaises(RuntimeError):
            validate_production_security()
