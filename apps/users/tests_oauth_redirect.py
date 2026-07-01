"""Open-redirect guard tests for the OAuth post-auth redirect (L-1)."""
from __future__ import annotations

from django.test import TestCase, override_settings
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from apps.users.auth_views import _safe_frontend_redirect


@override_settings(CORS_ALLOWED_ORIGINS=["https://app.example.com"])
class SafeFrontendRedirectTests(TestCase):
    def test_allows_same_origin_relative_path(self):
        self.assertEqual(_safe_frontend_redirect("/onboarding?next=/x"), "/onboarding?next=/x")

    def test_allows_allowlisted_origin(self):
        url = "https://app.example.com/auth/callback"
        self.assertEqual(_safe_frontend_redirect(url), url)

    def test_rejects_unlisted_absolute_origin(self):
        self.assertIsNone(_safe_frontend_redirect("https://evil.example.net/steal"))

    def test_rejects_protocol_relative(self):
        self.assertIsNone(_safe_frontend_redirect("//evil.example.net/steal"))

    def test_rejects_backslash_obfuscation(self):
        self.assertIsNone(_safe_frontend_redirect("/\\evil.example.net"))

    def test_rejects_empty_and_none(self):
        self.assertIsNone(_safe_frontend_redirect(""))
        self.assertIsNone(_safe_frontend_redirect(None))


@override_settings(
    SUPABASE_URL="https://proj.supabase.co",
    SUPABASE_OAUTH_REDIRECT_URI="https://api.example.com/api/auth/oauth/callback/",
    CORS_ALLOWED_ORIGINS=["https://app.example.com"],
)
class OAuthStartRedirectValidationTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.url = reverse("auth-oauth-start")

    def test_rejects_disallowed_redirect_uri_after(self):
        resp = self.client.post(
            self.url,
            {"provider": "google", "redirect_uri_after": "https://evil.example.net/x"},
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(resp.data.get("error_code"), "invalid_redirect")

    def test_accepts_allowlisted_redirect_uri_after(self):
        resp = self.client.post(
            self.url,
            {
                "provider": "google",
                "redirect_uri_after": "https://app.example.com/auth/callback",
            },
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertIn("authorize_url", resp.data)
