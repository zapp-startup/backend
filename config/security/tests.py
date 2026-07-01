from django.test import SimpleTestCase, override_settings

from config.security.production_validation import validate_production_security
from config.settings import development as development_settings


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
        SUPABASE_URL="https://project.supabase.co",
        SUPABASE_JWT_ISS="https://project.supabase.co/auth/v1",
        PLAID_WEBHOOK_URL="https://api.example.com/plaid/webhook",
        PLAID_REDIRECT_URI="https://app.example.com/auth/plaid/callback",
        PLAID_TOKEN_ENCRYPTION_KEY="8bUpWwzYgUN7ctklDvqGELWMKhfYbsxxNaKzUknYI5Q=",
        APP_DATA_ENCRYPTION_KEY="8bUpWwzYgUN7ctklDvqGELWMKhfYbsxxNaKzUknYI5Q=",
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
        SUPABASE_URL="https://project.supabase.co",
        SUPABASE_JWT_ISS="https://project.supabase.co/auth/v1",
        PLAID_WEBHOOK_URL="http://api.example.com/plaid/webhook",
        PLAID_TOKEN_ENCRYPTION_KEY="8bUpWwzYgUN7ctklDvqGELWMKhfYbsxxNaKzUknYI5Q=",
        APP_DATA_ENCRYPTION_KEY="8bUpWwzYgUN7ctklDvqGELWMKhfYbsxxNaKzUknYI5Q=",
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
        SUPABASE_URL="https://project.supabase.co",
        SUPABASE_JWT_ISS="https://project.supabase.co/auth/v1",
        PLAID_REDIRECT_URI="https://127.0.0.1:3000/plaid/callback",
        PLAID_TOKEN_ENCRYPTION_KEY="8bUpWwzYgUN7ctklDvqGELWMKhfYbsxxNaKzUknYI5Q=",
        APP_DATA_ENCRYPTION_KEY="8bUpWwzYgUN7ctklDvqGELWMKhfYbsxxNaKzUknYI5Q=",
    )
    def test_validate_fails_when_plaid_redirect_uri_points_to_loopback(self):
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
        SUPABASE_URL="http://project.supabase.co",
        SUPABASE_JWT_ISS="https://project.supabase.co/auth/v1",
        PLAID_TOKEN_ENCRYPTION_KEY="8bUpWwzYgUN7ctklDvqGELWMKhfYbsxxNaKzUknYI5Q=",
        APP_DATA_ENCRYPTION_KEY="8bUpWwzYgUN7ctklDvqGELWMKhfYbsxxNaKzUknYI5Q=",
    )
    def test_validate_fails_when_supabase_url_is_not_https(self):
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
        SUPABASE_URL="https://project.supabase.co",
        SUPABASE_JWT_ISS="https://127.0.0.1/auth/v1",
        PLAID_TOKEN_ENCRYPTION_KEY="8bUpWwzYgUN7ctklDvqGELWMKhfYbsxxNaKzUknYI5Q=",
        APP_DATA_ENCRYPTION_KEY="8bUpWwzYgUN7ctklDvqGELWMKhfYbsxxNaKzUknYI5Q=",
    )
    def test_validate_fails_when_supabase_jwt_issuer_points_to_loopback(self):
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
        SUPABASE_URL="https://project.supabase.co",
        SUPABASE_JWT_ISS="https://project.supabase.co/auth/v1",
        PLAID_TOKEN_ENCRYPTION_KEY="8bUpWwzYgUN7ctklDvqGELWMKhfYbsxxNaKzUknYI5Q=",
        APP_DATA_ENCRYPTION_KEY="",
    )
    def test_validate_fails_when_app_data_key_missing(self):
        with self.assertRaises(RuntimeError):
            validate_production_security()


class DevelopmentSettingsTests(SimpleTestCase):
    def test_frontend_dev_origins_allow_127_loopback_vite_and_alt_ports(self):
        expected = {
            "http://127.0.0.1:3000",
            "http://127.0.0.1:4173",
            "http://127.0.0.1:5173",
        }
        self.assertTrue(expected.issubset(set(development_settings.CORS_ALLOWED_ORIGINS)))
        self.assertTrue(expected.issubset(set(development_settings.CSRF_TRUSTED_ORIGINS)))

    def test_development_runs_csp_in_report_only_mode(self):
        self.assertTrue(development_settings.CONTENT_SECURITY_POLICY_REPORT_ONLY)


class SecurityHeadersMiddlewareTests(SimpleTestCase):
    """The middleware adds CSP / Permissions-Policy to every response."""

    @override_settings(
        CONTENT_SECURITY_POLICY="default-src 'none'",
        CONTENT_SECURITY_POLICY_REPORT_ONLY=False,
    )
    def test_enforcing_csp_header_present(self):
        resp = self.client.get("/healthz/")
        self.assertEqual(resp["Content-Security-Policy"], "default-src 'none'")
        self.assertNotIn("Content-Security-Policy-Report-Only", resp)
        self.assertIn("Permissions-Policy", resp)
        self.assertEqual(resp["X-Content-Type-Options"], "nosniff")

    @override_settings(
        CONTENT_SECURITY_POLICY="default-src 'none'",
        CONTENT_SECURITY_POLICY_REPORT_ONLY=True,
    )
    def test_report_only_csp_header_present(self):
        resp = self.client.get("/healthz/")
        self.assertEqual(
            resp["Content-Security-Policy-Report-Only"], "default-src 'none'"
        )
        self.assertNotIn("Content-Security-Policy", resp)
