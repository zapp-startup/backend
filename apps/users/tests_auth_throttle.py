"""Brute-force throttle tests for the credential endpoints."""
from __future__ import annotations

from unittest.mock import patch

from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from apps.users.session_auth import SupabaseAuthError


class LoginThrottleTests(TestCase):
    def setUp(self):
        cache.clear()
        self.client = APIClient()

    def tearDown(self):
        cache.clear()

    @patch("apps.users.auth_views.supabase_login")
    def test_repeated_failed_logins_are_throttled(self, mock_login):
        # Simulate invalid credentials so each allowed attempt returns 400
        # without touching Supabase. The dedicated throttle is 5/minute.
        mock_login.side_effect = SupabaseAuthError(
            "Invalid login credentials", error_code="invalid_credentials"
        )
        payload = {"email": "victim@example.com", "password": "guess"}
        url = reverse("auth-login")

        first_five = [
            self.client.post(url, payload, format="json").status_code
            for _ in range(5)
        ]
        sixth = self.client.post(url, payload, format="json")

        self.assertEqual(
            first_five,
            [status.HTTP_400_BAD_REQUEST] * 5,
            f"expected 5 rejected-but-allowed attempts, got {first_five}",
        )
        self.assertEqual(sixth.status_code, status.HTTP_429_TOO_MANY_REQUESTS)

    @patch("apps.users.auth_views.supabase_login")
    def test_throttle_is_scoped_per_email(self, mock_login):
        mock_login.side_effect = SupabaseAuthError(
            "Invalid login credentials", error_code="invalid_credentials"
        )
        url = reverse("auth-login")
        for _ in range(5):
            self.client.post(
                url, {"email": "a@example.com", "password": "x"}, format="json"
            )
        # A different email has its own bucket and is still allowed.
        other = self.client.post(
            url, {"email": "b@example.com", "password": "x"}, format="json"
        )
        self.assertEqual(other.status_code, status.HTTP_400_BAD_REQUEST)


class SignupThrottleTests(TestCase):
    def setUp(self):
        cache.clear()
        self.client = APIClient()

    def tearDown(self):
        cache.clear()

    @patch("apps.users.auth_views.supabase_signup")
    def test_repeated_signups_are_throttled(self, mock_signup):
        # No access_token -> email-confirmation path -> 200 without network.
        mock_signup.return_value = {}
        url = reverse("auth-signup")
        allowed = [
            self.client.post(
                url,
                {"email": f"u{i}@example.com", "password": "pw-long-enough"},
                format="json",
            ).status_code
            for i in range(10)
        ]
        eleventh = self.client.post(
            url, {"email": "u10@example.com", "password": "pw-long-enough"}, format="json"
        )
        self.assertTrue(all(s == status.HTTP_200_OK for s in allowed), allowed)
        self.assertEqual(eleventh.status_code, status.HTTP_429_TOO_MANY_REQUESTS)
