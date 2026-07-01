from unittest.mock import patch

from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.banking.categories import ZappPrimaryCategory, ZappSubcategory
from apps.compliance.models import AuditEvent, ConsentType
from apps.compliance.services import record_financial_consent
from apps.banking.models import BankAccount, BankConnection, BankTransaction, MerchantCategoryRule
from apps.banking.services.categorization_service import apply_merchant_override_rules
from apps.banking.lifecycle import purge_user_bank_data
from apps.banking.services.plaid_service import (
    exchange_public_token_for_user,
    sync_transactions_for_connection,
    upsert_transactions_from_plaid,
)
from apps.users.models import User
from apps.transactions.models import Transaction


class BankingAPITestCase(TestCase):
    """Basic tests for banking API endpoints."""

    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(
            username="banking_test",
            email="banking@test.com",
            password="testpass123",
        )

    @patch("apps.banking.views.create_link_token_for_user")
    def test_link_token_requires_auth(self, mock_create):
        """Link token endpoint requires authentication."""
        response = self.client.post("/api/banking/link-token/")
        self.assertIn(response.status_code, (401, 403))

    @override_settings(BANKING_REQUIRE_MFA=False, BANKING_REQUIRE_FINANCIAL_CONSENT=False)
    @patch("apps.banking.views.create_link_token_for_user")
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
    @patch("apps.banking.views.create_link_token_for_user")
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
        event = AuditEvent.objects.get(event_name="banking.policy_denied")
        self.assertEqual(event.error_code, "mfa_not_enrolled")
        self.assertEqual(event.metadata["decision"], "blocked")

    @override_settings(BANKING_REQUIRE_MFA=True, BANKING_REQUIRE_FINANCIAL_CONSENT=False)
    @patch("apps.banking.views.create_link_token_for_user")
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
    @patch("apps.banking.views.create_link_token_for_user")
    def test_link_token_aal2_allowed_when_mfa_required(self, mock_create):
        mock_create.return_value = "tok"
        self.client.force_authenticate(
            user=self.user,
            token={"aal": "aal2", "mfa_factors_count": -1, "amr": []},
        )
        res = self.client.post("/api/banking/link-token/")
        self.assertEqual(res.status_code, 200)

    @override_settings(BANKING_REQUIRE_MFA=True, BANKING_REQUIRE_FINANCIAL_CONSENT=True)
    @patch("apps.banking.views.create_link_token_for_user")
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
        event = AuditEvent.objects.get(event_name="banking.policy_denied")
        self.assertEqual(event.error_code, "financial_consent_required")

    @override_settings(BANKING_REQUIRE_MFA=True, BANKING_REQUIRE_FINANCIAL_CONSENT=True)
    @patch("apps.banking.views.create_link_token_for_user")
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

    @override_settings(BANKING_REQUIRE_MFA=True, BANKING_REQUIRE_FINANCIAL_CONSENT=False)
    def test_transactions_denied_when_mfa_not_satisfied(self):
        self.client.force_authenticate(
            user=self.user,
            token={"aal": "aal1", "mfa_factors_count": 0, "amr": []},
        )
        response = self.client.get("/api/banking/transactions/")
        self.assertEqual(response.status_code, 403)
        body = response.json()
        code = body.get("code") or (body.get("detail") or {}).get("code")
        self.assertEqual(code, "mfa_not_enrolled")

    @override_settings(BANKING_REQUIRE_MFA=False, BANKING_REQUIRE_FINANCIAL_CONSENT=False)
    @patch("apps.valuations.services.transaction_value_score.get_value_score_model")
    def test_bank_transaction_score_endpoint_persists_value_score_via_feedback_transaction(self, mock_get_model):
        class DummyModel:
            def predict(self, _data):
                import pandas as pd

                return pd.DataFrame(
                    [
                        {
                            "subscription_id": bank_transaction.id,
                            "value_score": 96,
                            "base_value_score": 90,
                            "confidence": 0.66,
                            "tier_used": 1,
                        }
                    ]
                )

        connection = BankConnection.objects.create(
            user=self.user,
            plaid_item_id="item-score",
            plaid_access_token="access-token",
        )
        account = BankAccount.objects.create(
            connection=connection,
            plaid_account_id="acct-score",
            name="Checking",
        )
        bank_transaction = BankTransaction.objects.create(
            user=self.user,
            connection=connection,
            account=account,
            plaid_transaction_id="plaid-txn-score",
            name="Coffee Shop",
            merchant_name="Coffee Shop",
            amount=8.50,
            date="2026-04-08",
            pending=False,
        )
        mock_get_model.return_value = DummyModel()
        self.client.force_authenticate(
            user=self.user,
            token={"aal": "aal1", "mfa_factors_count": -1, "amr": []},
        )

        response = self.client.post(f"/api/banking/transactions/{bank_transaction.id}/score/")

        self.assertEqual(response.status_code, 200)
        mirrored = Transaction.objects.get(bank_transaction=bank_transaction)
        self.assertIsNotNone(mirrored.personal_value_score)
        self.assertEqual(response.json()["personal_value_score"], mirrored.personal_value_score)

    @override_settings(BANKING_REQUIRE_MFA=True, BANKING_REQUIRE_FINANCIAL_CONSENT=False)
    @patch("apps.banking.views.sync_transactions_for_connection")
    def test_manual_sync_denied_when_mfa_not_satisfied(self, mock_sync):
        connection = BankConnection.objects.create(
            user=self.user,
            plaid_item_id="item-manual-sync",
            plaid_access_token="access-token",
        )
        self.client.force_authenticate(
            user=self.user,
            token={"aal": "aal1", "mfa_factors_count": 0, "amr": []},
        )
        response = self.client.post(f"/api/banking/connections/{connection.id}/sync/")
        self.assertEqual(response.status_code, 403)
        mock_sync.assert_not_called()


class PlaidServiceTestCase(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="plaid_test",
            email="plaid@test.com",
            password="testpass123",
        )

    def test_upsert_accounts_minimizes_stored_raw_payload(self):
        connection = BankConnection.objects.create(
            user=self.user,
            plaid_item_id="item-accounts-raw",
            plaid_access_token="access-token",
        )

        from apps.banking.services.plaid_service import upsert_accounts_from_plaid

        accounts = upsert_accounts_from_plaid(
            connection,
            [
                {
                    "account_id": "acct-1",
                    "name": "Primary Checking",
                    "official_name": "Primary Checking",
                    "mask": "1234",
                    "type": "depository",
                    "subtype": "checking",
                    "balances": {"current": 12.34, "available": 11.11, "iso_currency_code": "USD"},
                    "owners": [{"names": ["Sensitive Owner"]}],
                    "verification_status": "automatically_verified",
                }
            ],
        )

        self.assertEqual(len(accounts), 1)
        stored_payload = accounts[0].raw_payload
        self.assertIn("account_id", stored_payload)
        self.assertIn("verification_status", stored_payload)
        self.assertNotIn("owners", stored_payload)
        self.assertNotIn("name", stored_payload)

    def test_upsert_transactions_minimizes_stored_raw_payload(self):
        connection = BankConnection.objects.create(
            user=self.user,
            plaid_item_id="item-transactions-raw",
            plaid_access_token="access-token",
        )
        account = BankAccount.objects.create(
            connection=connection,
            plaid_account_id="acct-1",
            name="Checking",
        )

        upsert_transactions_from_plaid(
            connection,
            {
                "added": [
                    {
                        "transaction_id": "txn-1",
                        "account_id": account.plaid_account_id,
                        "name": "Coffee Shop",
                        "merchant_name": "Coffee Shop",
                        "amount": 4.25,
                        "date": "2026-03-20",
                        "pending": False,
                        "payment_channel": "in store",
                        "location": {"address": "123 Main St"},
                        "personal_finance_category": {"primary": "FOOD_AND_DRINK"},
                    }
                ],
                "modified": [],
                "removed": [],
            },
        )

        txn = connection.transactions.get(plaid_transaction_id="txn-1")
        self.assertIn("transaction_id", txn.raw_payload)
        self.assertIn("payment_channel", txn.raw_payload)
        self.assertIn("personal_finance_category", txn.raw_payload)
        self.assertNotIn("location", txn.raw_payload)
        self.assertNotIn("merchant_name", txn.raw_payload)

    @patch("apps.banking.services.plaid_service.fetch_accounts_for_connection")
    @patch("apps.banking.services.plaid_service._get_plaid_client")
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

    @patch("apps.banking.services.plaid_service.upsert_transactions_from_plaid")
    @patch("apps.banking.services.plaid_service._get_plaid_client")
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

    @patch("apps.banking.services.plaid_service._upsert_single_transaction")
    @patch("apps.banking.services.plaid_service.fetch_accounts_for_connection")
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
        from apps.banking.models import BankConnection

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


# New tests for bank transaction feedback and user-state inference are in
# banking/tests_workflow.py (no plaid dependency, runnable in all environments).
