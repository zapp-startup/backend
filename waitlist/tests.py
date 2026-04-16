from copy import deepcopy

from django.conf import settings
from django.core.cache import cache
from django.test import override_settings
from rest_framework import status
from rest_framework.test import APITestCase

from .models import WaitlistSignup

REST_FRAMEWORK_WAITLIST_THROTTLE_REGRESSION = {
    **deepcopy(settings.REST_FRAMEWORK),
    "DEFAULT_THROTTLE_RATES": {
        **deepcopy(settings.REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"]),
        "anon": "1/minute",
        "waitlist_signup": "10/minute",
    },
}


class WaitlistSignupViewTests(APITestCase):
    def setUp(self):
        cache.clear()

    def test_creates_waitlist_signup(self):
        response = self.client.post(
            "/api/waitlist-signups/",
            {
                "name": "Siddhant",
                "email": "Siddhant@Example.com",
                "source": "landing_page",
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertTrue(response.data["created"])
        self.assertEqual(response.data["email"], "siddhant@example.com")
        self.assertEqual(WaitlistSignup.objects.count(), 1)

    def test_reuses_existing_email_without_creating_duplicate(self):
        WaitlistSignup.objects.create(
            name="Old Name",
            email="hello@example.com",
            source="landing_page",
        )

        response = self.client.post(
            "/api/waitlist-signups/",
            {
                "name": "New Name",
                "email": "hello@example.com",
                "source": "landing_page",
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertFalse(response.data["created"])
        self.assertEqual(WaitlistSignup.objects.count(), 1)
        self.assertEqual(WaitlistSignup.objects.get().name, "New Name")

    def test_requires_name_and_email(self):
        response = self.client.post(
            "/api/waitlist-signups/",
            {
                "name": "",
                "email": "",
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("name", response.data)
        self.assertIn("email", response.data)

    def test_ignores_invalid_bearer_token_for_public_signup(self):
        self.client.credentials(HTTP_AUTHORIZATION="Bearer expired-or-invalid-token")

        response = self.client.post(
            "/api/waitlist-signups/",
            {
                "name": "Public User",
                "email": "public@example.com",
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertTrue(response.data["created"])
        self.assertEqual(response.data["email"], "public@example.com")

    def test_rejects_malformed_email_addresses(self):
        response = self.client.post(
            "/api/waitlist-signups/",
            {
                "name": "Bad Email",
                "email": "not-an-email",
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("email", response.data)

    @override_settings(REST_FRAMEWORK=REST_FRAMEWORK_WAITLIST_THROTTLE_REGRESSION)
    def test_waitlist_signup_uses_dedicated_throttle_scope(self):
        first = self.client.post(
            "/api/waitlist-signups/",
            {
                "name": "First User",
                "email": "first@example.com",
            },
            format="json",
        )
        second = self.client.post(
            "/api/waitlist-signups/",
            {
                "name": "Second User",
                "email": "second@example.com",
            },
            format="json",
        )

        self.assertEqual(first.status_code, status.HTTP_201_CREATED)
        self.assertEqual(second.status_code, status.HTTP_201_CREATED)
