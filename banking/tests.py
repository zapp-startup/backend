from unittest.mock import patch

from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from banking.categories import ZappPrimaryCategory, ZappSubcategory
from compliance.models import ConsentType
from compliance.services import record_financial_consent
from banking.models import BankAccount, BankConnection, MerchantCategoryRule
from banking.services.categorization_service import apply_merchant_override_rules
from banking.lifecycle import purge_user_bank_data
from banking.services.plaid_service import (
    exchange_public_token_for_user,
    sync_transactions_for_connection,
    upsert_transactions_from_plaid,
)
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
        self.assertIn(response.status_code, (401, 403))

    @override_settings(BANKING_REQUIRE_MFA=False, BANKING_REQUIRE_FINANCIAL_CONSENT=False)
    @patch("banking.views.create_link_token_for_user")
    def test_link_token_returns_token_when_authenticated(self, mock_create):
        """Relaxed policy: link_token succeeds without aal2 (dev-default-style)."""
        mock_create.return_value = "link-sandbox-abc123"
        self.client.force_authenticate(
            user=self.user,
            token={"aal": "aal1", "mfa_factors_count": 0, "amr": []},
        )
        response = self.client.post("/api/banking/link-token/")
        self.assertEqual(response.status_code, 200)
        self.assertIn("link_token", response.json())
        self.assertEqual(response.json()["link_token"], "link-sandbox-abc123")

    @override_settings(BANKING_REQUIRE_MFA=True, BANKING_REQUIRE_FINANCIAL_CONSENT=False)
    @patch("banking.views.create_link_token_for_user")
    def test_link_token_denied_when_mfa_not_satisfied(self, mock_create):
        mock_create.return_value = "tok"
        self.client.force_authenticate(
            user=self.user,
            token={"aal": "aal1", "mfa_factors_count": 0, "amr": []},
        )
        res = self.client.post("/api/banking/link-token/")
        self.assertEqual(res.status_code, 403)
        body = res.json()
        code = body.get("code") or (body.get("detail") or {}).get("code")
        self.assertEqual(code, "mfa_not_enrolled")

    @override_settings(BANKING_REQUIRE_MFA=True, BANKING_REQUIRE_FINANCIAL_CONSENT=False)
    @patch("banking.views.create_link_token_for_user")
    def test_link_token_denied_aal1_unknown_factor_count(self, mock_create):
        mock_create.return_value = "tok"
        self.client.force_authenticate(
            user=self.user,
            token={"aal": "aal1", "mfa_factors_count": -1, "amr": []},
        )
        res = self.client.post("/api/banking/link-token/")
        self.assertEqual(res.status_code, 403)
        body = res.json()
        code = body.get("code") or (body.get("detail") or {}).get("code")
        self.assertEqual(code, "mfa_required")

    @override_settings(BANKING_REQUIRE_MFA=True, BANKING_REQUIRE_FINANCIAL_CONSENT=False)
    @patch("banking.views.create_link_token_for_user")
    def test_link_token_aal2_allowed_when_mfa_required(self, mock_create):
        mock_create.return_value = "tok"
        self.client.force_authenticate(
            user=self.user,
            token={"aal": "aal2", "mfa_factors_count": -1, "amr": []},
        )
        res = self.client.post("/api/banking/link-token/")
        self.assertEqual(res.status_code, 200)

    @override_settings(BANKING_REQUIRE_MFA=True, BANKING_REQUIRE_FINANCIAL_CONSENT=True)
    @patch("banking.views.create_link_token_for_user")
    def test_link_token_denied_without_consent(self, mock_create):
        mock_create.return_value = "tok"
        self.client.force_authenticate(
            user=self.user,
            token={"aal": "aal2", "mfa_factors_count": 1, "amr": ["pwd", "otp"]},
        )
        res = self.client.post("/api/banking/link-token/")
        self.assertEqual(res.status_code, 428)
        body = res.json()
        code = body.get("code") or (body.get("detail") or {}).get("code")
        self.assertEqual(code, "financial_consent_required")

    @override_settings(BANKING_REQUIRE_MFA=True, BANKING_REQUIRE_FINANCIAL_CONSENT=True)
    @patch("banking.views.create_link_token_for_user")
    def test_link_token_allowed_with_mfa_and_consent(self, mock_create):
        mock_create.return_value = "tok"
        record_financial_consent(user=self.user, consent_type=ConsentType.FINANCIAL_DATA_ACCESS, source="test")
        self.client.force_authenticate(
            user=self.user,
            token={"aal": "aal2", "mfa_factors_count": 1, "amr": ["pwd", "otp"]},
        )
        res = self.client.post("/api/banking/link-token/")
        self.assertEqual(res.status_code, 200)

    def test_exchange_token_requires_auth(self):
        """Exchange token endpoint requires authentication."""
        response = self.client.post(
            "/api/banking/exchange-token/",
            {"public_token": "public-sandbox-xyz"},
            format="json",
        )
        self.assertIn(response.status_code, (401, 403))

    @override_settings(BANKING_REQUIRE_MFA=False, BANKING_REQUIRE_FINANCIAL_CONSENT=False)
    def test_exchange_token_requires_public_token(self):
        """Exchange token requires public_token in body."""
        self.client.force_authenticate(
            user=self.user,
            token={"aal": "aal1", "mfa_factors_count": -1, "amr": []},
        )
        response = self.client.post(
            "/api/banking/exchange-token/",
            {},
            format="json",
        )
        self.assertEqual(response.status_code, 400)


class PlaidServiceTestCase(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="plaid_test",
            email="plaid@test.com",
            password="testpass123",
        )

    @patch("banking.services.plaid_service.fetch_accounts_for_connection")
    @patch("banking.services.plaid_service._get_plaid_client")
    def test_exchange_rolls_back_connection_on_initial_import_failure(
        self,
        mock_get_client,
        mock_fetch_accounts,
    ):
        client = mock_get_client.return_value
        client.item_public_token_exchange.return_value = {
            "access_token": "access-token",
            "item_id": "item-123",
        }
        mock_fetch_accounts.side_effect = RuntimeError("accounts failed")

        with self.assertRaises(RuntimeError):
            exchange_public_token_for_user(self.user, "public-token")

        self.assertEqual(BankConnection.objects.count(), 0)

    @patch("banking.services.plaid_service.upsert_transactions_from_plaid")
    @patch("banking.services.plaid_service._get_plaid_client")
    def test_sync_keeps_original_cursor_when_paginated_sync_fails(
        self,
        mock_get_client,
        mock_upsert,
    ):
        connection = BankConnection.objects.create(
            user=self.user,
            plaid_item_id="item-cursor",
            plaid_access_token="access-token",
            sync_cursor="cursor-original",
        )
        client = mock_get_client.return_value
        client.transactions_sync.side_effect = [
            {
                "added": [],
                "modified": [],
                "removed": [],
                "next_cursor": "cursor-page-1",
                "has_more": True,
            },
            RuntimeError("page 2 failed"),
        ]

        with self.assertRaises(RuntimeError):
            sync_transactions_for_connection(connection)

        connection.refresh_from_db()
        self.assertEqual(connection.sync_cursor, "cursor-original")
        self.assertFalse(connection.last_synced_at)
        self.assertEqual(mock_upsert.call_count, 1)

    @patch("banking.services.plaid_service._upsert_single_transaction")
    @patch("banking.services.plaid_service.fetch_accounts_for_connection")
    def test_upsert_refreshes_accounts_before_dropping_unknown_account_transactions(
        self,
        mock_fetch_accounts,
        mock_upsert_single,
    ):
        connection = BankConnection.objects.create(
            user=self.user,
            plaid_item_id="item-upsert",
            plaid_access_token="access-token",
        )
        BankAccount.objects.create(
            connection=connection,
            plaid_account_id="acct-known",
            name="Checking",
        )

        def add_missing_account(_connection):
            return [
                BankAccount.objects.update_or_create(
                    connection=connection,
                    plaid_account_id="acct-new",
                    defaults={"name": "Savings"},
                )[0]
            ]

        mock_fetch_accounts.side_effect = add_missing_account

        upsert_transactions_from_plaid(
            connection,
            {
                "added": [{"transaction_id": "txn-1", "account_id": "acct-new"}],
                "modified": [],
                "removed": [],
            },
        )

        self.assertEqual(mock_fetch_accounts.call_count, 1)
        self.assertEqual(mock_upsert_single.call_count, 1)
        account_map = mock_upsert_single.call_args[0][2]
        self.assertIn("acct-new", account_map)


class BankingLifecycleTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="purge_u",
            email="purge@test.com",
            password="testpass123",
        )

    def test_purge_removes_connections(self):
        from banking.models import BankConnection

        BankConnection.objects.create(
            user=self.user,
            plaid_item_id="item-1",
            plaid_access_token="tok",
        )
        self.assertEqual(BankConnection.objects.filter(user=self.user).count(), 1)
        purge_user_bank_data(self.user)
        self.assertEqual(BankConnection.objects.filter(user=self.user).count(), 0)


class CategorizationServiceTestCase(TestCase):
    def test_merchant_override_prefers_more_specific_pattern_with_same_priority(self):
        MerchantCategoryRule.objects.create(
            match_pattern="uber",
            priority=100,
            zapp_primary_category=ZappPrimaryCategory.TRANSPORTATION,
            zapp_subcategory=ZappSubcategory.RIDE_SHARE,
            is_active=True,
        )
        MerchantCategoryRule.objects.create(
            match_pattern="uber eats",
            priority=100,
            zapp_primary_category=ZappPrimaryCategory.DINING_CAFES,
            zapp_subcategory=ZappSubcategory.DELIVERY,
            is_active=True,
        )

        result = apply_merchant_override_rules("uber eats 1234")

        self.assertEqual(
            result,
            (ZappPrimaryCategory.DINING_CAFES, ZappSubcategory.DELIVERY),
        )
