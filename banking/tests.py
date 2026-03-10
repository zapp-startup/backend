from unittest.mock import patch

from django.test import TestCase
from rest_framework.test import APIClient

from users.models import User


class BankingAPITestCase(TestCase):
    """Basic tests for banking API endpoints."""

    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(
            username="banking_test",
            email="banking@test.com",
            password="testpass123",
        )

    @patch("banking.views.create_link_token_for_user")
    def test_link_token_requires_auth(self, mock_create):
        """Link token endpoint requires authentication."""
        response = self.client.post("/api/banking/link-token/")
        self.assertEqual(response.status_code, 401)

    @patch("banking.views.create_link_token_for_user")
    def test_link_token_returns_token_when_authenticated(self, mock_create):
        """Link token returns link_token when authenticated."""
        mock_create.return_value = "link-sandbox-abc123"
        self.client.force_authenticate(user=self.user)
        response = self.client.post("/api/banking/link-token/")
        self.assertEqual(response.status_code, 200)
        self.assertIn("link_token", response.json())
        self.assertEqual(response.json()["link_token"], "link-sandbox-abc123")

    def test_exchange_token_requires_auth(self):
        """Exchange token endpoint requires authentication."""
        response = self.client.post(
            "/api/banking/exchange-token/",
            {"public_token": "public-sandbox-xyz"},
            format="json",
        )
        self.assertEqual(response.status_code, 401)

    def test_exchange_token_requires_public_token(self):
        """Exchange token requires public_token in body."""
        self.client.force_authenticate(user=self.user)
        response = self.client.post(
            "/api/banking/exchange-token/",
            {},
            format="json",
        )
        self.assertEqual(response.status_code, 400)
