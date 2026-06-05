"""
Tests for bank transaction feedback workflow and user-state inference.

Kept in a separate file from banking/tests.py because tests.py imports
banking.services.plaid_service which requires the 'plaid' library; these tests
do not depend on plaid and should run in all environments.
"""
from datetime import timedelta

from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from apps.banking.categories import ZappPrimaryCategory
from apps.banking.models import BankAccount, BankConnection, BankTransaction
from apps.users.models import User


class BankTransactionFeedbackEndpointTestCase(TestCase):
    """Tests for PATCH /api/banking/transactions/<plaid_id>/ feedback endpoint."""

    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(
            username="bank_feedback_test",
            email="bankfb@test.com",
            password="testpass123",
        )
        connection = BankConnection.objects.create(
            user=self.user,
            plaid_item_id="item-fb",
            plaid_access_token="access-token",
        )
        account = BankAccount.objects.create(
            connection=connection,
            plaid_account_id="acct-fb",
            name="Checking",
        )
        self.bank_transaction = BankTransaction.objects.create(
            user=self.user,
            connection=connection,
            account=account,
            plaid_transaction_id="plaid-txn-fb",
            name="Walgreens",
            merchant_name="Walgreens",
            amount=12.50,
            date="2026-04-01",
            zapp_primary_category=ZappPrimaryCategory.HEALTH_WELLNESS,
            pending=False,
        )

    @override_settings(BANKING_REQUIRE_MFA=False, BANKING_REQUIRE_FINANCIAL_CONSENT=False)
    def test_feedback_patch_creates_feedback_transaction(self):
        self.client.force_authenticate(
            user=self.user,
            token={"aal": "aal1", "mfa_factors_count": -1, "amr": []},
        )
        response = self.client.patch(
            f"/api/banking/transactions/{self.bank_transaction.plaid_transaction_id}/",
            {
                "satisfaction_rating": 7,
                "regret_rating": 20,
                "repurchase_likelihood": 80,
                "reflection_text": "Good value for the medicine",
            },
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        from apps.transactions.models import Transaction
        fb_tx = Transaction.objects.get(bank_transaction=self.bank_transaction)
        self.assertEqual(fb_tx.satisfaction_rating, 7)
        self.assertEqual(fb_tx.regret_rating, 20)
        self.assertEqual(fb_tx.repurchase_likelihood, 80)
        self.assertEqual(fb_tx.reflection_text, "Good value for the medicine")
        # Feedback scoring derivation should have run
        self.assertIsNotNone(fb_tx.feedback_value_score)
        self.assertIsNotNone(fb_tx.feedback_confidence)

    @override_settings(BANKING_REQUIRE_MFA=False, BANKING_REQUIRE_FINANCIAL_CONSENT=False)
    def test_feedback_patch_preserves_health_category_from_zapp(self):
        """Bank txn with HEALTH_WELLNESS zapp category → feedback Transaction.category = health."""
        self.client.force_authenticate(
            user=self.user,
            token={"aal": "aal1", "mfa_factors_count": -1, "amr": []},
        )
        self.client.patch(
            f"/api/banking/transactions/{self.bank_transaction.plaid_transaction_id}/",
            {"satisfaction_rating": 8},
            format="json",
        )
        from apps.transactions.models import Transaction, TransactionCategory
        fb_tx = Transaction.objects.get(bank_transaction=self.bank_transaction)
        self.assertEqual(fb_tx.category, TransactionCategory.HEALTH)

    @override_settings(BANKING_REQUIRE_MFA=False, BANKING_REQUIRE_FINANCIAL_CONSENT=False)
    def test_feedback_patch_discards_bogus_considered_at(self):
        """considered_at >= occurred_at is discarded server-side."""
        self.client.force_authenticate(
            user=self.user,
            token={"aal": "aal1", "mfa_factors_count": -1, "amr": []},
        )
        future_considered_at = (timezone.now() + timedelta(hours=1)).isoformat()
        response = self.client.patch(
            f"/api/banking/transactions/{self.bank_transaction.plaid_transaction_id}/",
            {"satisfaction_rating": 7, "considered_at": future_considered_at},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        from apps.transactions.models import Transaction
        fb_tx = Transaction.objects.get(bank_transaction=self.bank_transaction)
        self.assertIsNone(fb_tx.considered_at)

    @override_settings(BANKING_REQUIRE_MFA=False, BANKING_REQUIRE_FINANCIAL_CONSENT=False)
    def test_feedback_patch_rejects_invalid_usage_frequency(self):
        self.client.force_authenticate(
            user=self.user,
            token={"aal": "aal1", "mfa_factors_count": -1, "amr": []},
        )
        response = self.client.patch(
            f"/api/banking/transactions/{self.bank_transaction.plaid_transaction_id}/",
            {"satisfaction_rating": 7, "usage_frequency": 999},
            format="json",
        )
        self.assertEqual(response.status_code, 400)

    @override_settings(BANKING_REQUIRE_MFA=False, BANKING_REQUIRE_FINANCIAL_CONSENT=False)
    def test_feedback_patch_requires_auth(self):
        response = self.client.patch(
            f"/api/banking/transactions/{self.bank_transaction.plaid_transaction_id}/",
            {"satisfaction_rating": 7},
            format="json",
        )
        self.assertIn(response.status_code, (401, 403))

    @override_settings(BANKING_REQUIRE_MFA=False, BANKING_REQUIRE_FINANCIAL_CONSENT=False)
    def test_feedback_patch_returns_404_for_unknown_plaid_id(self):
        self.client.force_authenticate(
            user=self.user,
            token={"aal": "aal1", "mfa_factors_count": -1, "amr": []},
        )
        response = self.client.patch(
            "/api/banking/transactions/nonexistent-plaid-id/",
            {"satisfaction_rating": 7},
            format="json",
        )
        self.assertEqual(response.status_code, 404)


class UserStateInferenceTestCase(TestCase):
    """Tests for user state inference robustness."""

    def setUp(self):
        self.user = User.objects.create_user(
            username="state-inference-user",
            email="si@test.com",
            password="testpass123",
        )

    def test_percent_income_on_subscriptions_computed_when_income_and_priced_subs_present(self):
        from decimal import Decimal
        from apps.subscriptions.models import Merchant, MerchantCategory, Subscription, SubscriptionStatus, BillingCycle
        from apps.users.models import UserRawExplicit
        from apps.users.services.state_inference import recompute_user_raw_inferred

        UserRawExplicit.objects.create(
            user=self.user,
            monthly_income=Decimal("5000.00"),
        )
        merchant = Merchant.objects.create(name="TestSubMerchant", category=MerchantCategory.STREAMING)
        Subscription.objects.create(
            user=self.user,
            merchant=merchant,
            price=Decimal("15.00"),
            billing_cycle=BillingCycle.MONTHLY,
            status=SubscriptionStatus.ACTIVE,
        )
        inferred = recompute_user_raw_inferred(self.user.id)
        self.assertIsNotNone(inferred.percent_income_spent_on_subscriptions)
        self.assertGreater(inferred.percent_income_spent_on_subscriptions, 0)

    def test_percent_income_null_when_no_subscriptions_and_no_income(self):
        from apps.users.services.state_inference import recompute_user_raw_inferred
        inferred = recompute_user_raw_inferred(self.user.id)
        self.assertIsNone(inferred.percent_income_spent_on_subscriptions)

    def test_percent_income_null_when_income_missing(self):
        """No income → percent unknown, not 0."""
        from decimal import Decimal
        from apps.subscriptions.models import Merchant, MerchantCategory, Subscription, SubscriptionStatus, BillingCycle
        from apps.users.services.state_inference import recompute_user_raw_inferred

        merchant = Merchant.objects.create(name="NoIncomeMerchant", category=MerchantCategory.STREAMING)
        Subscription.objects.create(
            user=self.user,
            merchant=merchant,
            price=Decimal("10.00"),
            billing_cycle=BillingCycle.MONTHLY,
            status=SubscriptionStatus.ACTIVE,
        )
        inferred = recompute_user_raw_inferred(self.user.id)
        self.assertIsNone(inferred.percent_income_spent_on_subscriptions)

    def test_percent_income_null_when_all_sub_prices_are_none(self):
        """
        When all active subscriptions have null price, the result must be null
        (not a spurious 0%) since we have no usable cost data.
        """
        from decimal import Decimal
        from unittest.mock import patch, MagicMock
        from apps.users.models import UserRawExplicit
        from apps.users.services.state_inference import recompute_user_raw_inferred
        from apps.subscriptions.models import SubscriptionStatus

        UserRawExplicit.objects.create(
            user=self.user,
            monthly_income=Decimal("5000.00"),
        )
        mock_sub = MagicMock()
        mock_sub.status = SubscriptionStatus.ACTIVE
        mock_sub.price = None
        mock_sub.billing_cycle = "monthly"
        mock_sub.usage_frequency = None
        mock_sub.reactivation_count = 0

        with patch("apps.users.services.state_inference.Subscription.objects.filter") as mock_filter:
            mock_filter.return_value = [mock_sub]
            inferred = recompute_user_raw_inferred(self.user.id)

        self.assertIsNone(inferred.percent_income_spent_on_subscriptions)

    def test_quick_decision_uses_current_transaction_time_not_previous(self):
        """
        Regression: quick_decision must use THIS transaction's decision time.
        Previously it used decision_times[-1] which could be from a prior transaction.
        """
        from apps.users.services.state_inference import recompute_user_raw_inferred
        from apps.transactions.models import Transaction, TransactionCategory, TransactionDirection

        now = timezone.now()

        # Transaction 1: slow decision (120 minutes)
        slow_occurred = now - timedelta(days=10)
        slow_considered = slow_occurred - timedelta(minutes=120)
        Transaction.objects.create(
            user=self.user,
            direction=TransactionDirection.SPEND,
            amount=50,
            occurred_at=slow_occurred,
            category=TransactionCategory.SHOPPING,
            considered_at=slow_considered,
        )

        # Transaction 2: no considered_at — should be independent of txn 1's time.
        Transaction.objects.create(
            user=self.user,
            direction=TransactionDirection.SPEND,
            amount=30,
            occurred_at=now - timedelta(days=5),
            category=TransactionCategory.SHOPPING,
        )

        inferred = recompute_user_raw_inferred(self.user.id)
        self.assertIsNotNone(inferred)
        # avg_decision_time_minutes should reflect only txn 1 (120 min)
        self.assertAlmostEqual(inferred.avg_decision_time_minutes, 120.0, delta=1.0)
