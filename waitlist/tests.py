from rest_framework import status
from rest_framework.test import APITestCase

from .models import WaitlistSignup


class WaitlistSignupViewTests(APITestCase):
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
