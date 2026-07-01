"""Regression tests for merchant-catalog exposure hardening (M-3)."""
from __future__ import annotations

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from apps.subscriptions.models import Merchant

User = get_user_model()


class MerchantCatalogExposureTests(TestCase):
    def setUp(self):
        self.merchant = Merchant.objects.create(
            name="Netflix",
            website_domain="netflix.com",
        )
        self.url = reverse("merchants-list")
        self.user = User.objects.create_user(username="member", password="pw")

    def test_anonymous_cannot_list_merchants(self):
        resp = APIClient().get(self.url)
        self.assertIn(
            resp.status_code,
            (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN),
        )

    def test_authenticated_user_gets_allowlisted_fields_only(self):
        client = APIClient()
        client.force_authenticate(self.user)
        resp = client.get(self.url)
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        rows = resp.data if isinstance(resp.data, list) else resp.data.get("results", [])
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(set(row.keys()), {"id", "name", "category"})
        # Internal/operator fields must not leak.
        self.assertNotIn("website_domain", row)
        self.assertNotIn("subscription_eligibility", row)
        self.assertNotIn("created_at", row)
