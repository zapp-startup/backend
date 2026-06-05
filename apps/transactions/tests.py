from datetime import timedelta
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.transactions.feedback_candidates import (
    _average_positive_amount,
    _category_weight,
    _recency_score,
    get_feedback_candidates,
)
from apps.transactions.models import Transaction, TransactionCategory, TransactionReflection
from apps.transactions.services.feedback_scoring import apply_feedback_scoring, derive_feedback_fields
from apps.users.models import UserComputed, UserRawExplicit
from apps.users.models import User


class FeedbackCandidatesTestCase(TestCase):
    """Tests for feedback candidate selection."""

    def setUp(self):
        self.user = User.objects.create_user(
            username="test-user",
            email="test@example.com",
            password="testpass123",
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    def test_category_weights(self):
        """High-signal categories score higher than low-signal."""
        assert _category_weight(TransactionCategory.EATING_OUT) > _category_weight(
            TransactionCategory.GROCERIES
        )
        assert _category_weight(TransactionCategory.SHOPPING) > _category_weight(
            TransactionCategory.BILLS
        )

    def test_recency_score(self):
        """Recent dates score higher."""
        now = timezone.now()
        today = now.date()
        assert _recency_score(now, now) == 1.0
        # 30 days ago should be lower
        from datetime import timedelta

        old = now - timedelta(days=30)
        assert _recency_score(old, now) < _recency_score(now, now)

    def test_average_positive_amount_ignores_non_positive_values(self):
        Transaction.objects.create(
            user=self.user,
            direction="spend",
            amount=0,
            occurred_at=timezone.now(),
            category=TransactionCategory.SHOPPING,
        )
        positive = Transaction.objects.create(
            user=self.user,
            direction="spend",
            amount=60,
            occurred_at=timezone.now(),
            category=TransactionCategory.SHOPPING,
        )

        avg = _average_positive_amount(Transaction.objects.filter(user=self.user))

        self.assertEqual(avg, positive.amount)

    def test_feedback_candidates_endpoint_returns_list(self):
        """GET /api/transactions/feedback-candidates/ returns a list."""
        response = self.client.get("/api/transactions/feedback-candidates/")
        self.assertEqual(response.status_code, 200)
        self.assertIsInstance(response.data, list)

    def test_feedback_candidates_excludes_transactions_with_feedback(self):
        """Transactions with existing feedback are not returned."""
        t = Transaction.objects.create(
            user=self.user,
            direction="spend",
            amount=50,
            occurred_at=timezone.now(),
            category=TransactionCategory.EATING_OUT,
            satisfaction_rating=8,
        )
        candidates = get_feedback_candidates(self.user, days_window=365, top_n=5)
        ids = [c["transaction_id"] for c in candidates]
        self.assertNotIn(t.id, ids)

    def test_feedback_candidates_returns_only_user_transactions(self):
        """Only the requesting user's transactions are considered."""
        other = User.objects.create_user(username="other-user", email="other@example.com", password="x")
        Transaction.objects.create(
            user=other,
            direction="spend",
            amount=100,
            occurred_at=timezone.now(),
            category=TransactionCategory.SHOPPING,
        )
        candidates = get_feedback_candidates(self.user, days_window=365, top_n=5)
        # Our user has no transactions, so candidates should be empty
        self.assertEqual(candidates, [])

    def test_feedback_candidates_uses_python_amount_average(self):
        Transaction.objects.create(
            user=self.user,
            direction="spend",
            amount=50,
            occurred_at=timezone.now(),
            category=TransactionCategory.EATING_OUT,
            satisfaction_rating=9,
        )
        target = Transaction.objects.create(
            user=self.user,
            direction="spend",
            amount=120,
            occurred_at=timezone.now(),
            category=TransactionCategory.SHOPPING,
        )

        candidates = get_feedback_candidates(self.user, days_window=365, top_n=5)

        self.assertIn(target.id, [candidate["transaction_id"] for candidate in candidates])

    def test_feedback_candidates_include_low_signal_transactions_when_only_options(self):
        transaction = Transaction.objects.create(
            user=self.user,
            direction="spend",
            amount=42,
            occurred_at=timezone.now(),
            category=TransactionCategory.GROCERIES,
        )

        candidates = get_feedback_candidates(self.user, days_window=365, top_n=5)

        self.assertIn(transaction.id, [candidate["transaction_id"] for candidate in candidates])

    def test_transaction_reflection_accepts_string_regret_score(self):
        transaction = Transaction.objects.create(
            user=self.user,
            direction="spend",
            amount=50,
            occurred_at=timezone.now(),
            category=TransactionCategory.EATING_OUT,
        )

        response = self.client.post(
            "/api/transaction-reflections/",
            {
                "transaction": transaction.id,
                "regret_score": "10",
                "was_worth_it": True,
                "notes": "solid",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 201)
        reflection = TransactionReflection.objects.get(pk=response.data["id"])
        self.assertEqual(reflection.regret_score, 10)

    def test_transaction_reflection_preserves_false_boolean(self):
        transaction = Transaction.objects.create(
            user=self.user,
            direction="spend",
            amount=25,
            occurred_at=timezone.now(),
            category=TransactionCategory.SHOPPING,
        )

        response = self.client.post(
            "/api/transaction-reflections/",
            {
                "transaction": transaction.id,
                "regret_score": 30,
                "was_worth_it": False,
                "notes": "duplicate",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 201)
        reflection = TransactionReflection.objects.get(pk=response.data["id"])
        self.assertIs(reflection.was_worth_it, False)

    @patch("apps.valuations.services.transaction_value_score.get_value_score_model")
    def test_transaction_score_endpoint_persists_value_score(self, mock_get_model):
        class DummyModel:
            def predict(self, _data):
                import pandas as pd

                return pd.DataFrame(
                    [
                        {
                            "subscription_id": transaction.id,
                            "value_score": 132,
                            "base_value_score": 128,
                            "confidence": 0.81,
                            "tier_used": 2,
                        }
                    ]
                )

        UserRawExplicit.objects.create(
            user=self.user,
            monthly_income=5000,
            budget_style="optimize_value",
            value_priority_quality=80,
        )
        UserComputed.objects.create(
            user=self.user,
            cost_weight=0.4,
            quality_weight=0.6,
            sustainability_weight=0.2,
            budget_adherence_score=0.8,
        )
        transaction = Transaction.objects.create(
            user=self.user,
            direction="spend",
            amount=120,
            occurred_at=timezone.now(),
            category=TransactionCategory.EDUCATION,
            description_raw="React course",
            satisfaction_rating=9,
            repurchase_likelihood=85,
        )
        mock_get_model.return_value = DummyModel()

        response = self.client.post(f"/api/transactions/{transaction.id}/score/")

        self.assertEqual(response.status_code, 200)
        transaction.refresh_from_db()
        self.assertIsNotNone(transaction.personal_value_score)
        self.assertGreaterEqual(transaction.personal_value_score, 0)
        self.assertLessEqual(transaction.personal_value_score, 150)
        self.assertEqual(response.data["personal_value_score"], transaction.personal_value_score)
        self.assertEqual(response.data["value_score_model_version"], "value_score@bundle")

    def test_transaction_score_endpoint_is_scoped_to_current_user(self):
        other = User.objects.create_user(username="scoped-user", email="scoped@example.com", password="x")
        transaction = Transaction.objects.create(
            user=other,
            direction="spend",
            amount=75,
            occurred_at=timezone.now(),
            category=TransactionCategory.SHOPPING,
        )

        response = self.client.post(f"/api/transactions/{transaction.id}/score/")

        self.assertEqual(response.status_code, 404)


class FeedbackScoringTestCase(TestCase):
    """Tests for backend-derived feedback field computation."""

    def setUp(self):
        self.user = User.objects.create_user(
            username="feedback-scoring-user",
            email="fs@example.com",
            password="testpass123",
        )

    def _make_txn(self, **kwargs):
        return Transaction.objects.create(
            user=self.user,
            direction="spend",
            amount=50,
            occurred_at=timezone.now(),
            category=TransactionCategory.SHOPPING,
            **kwargs,
        )

    def test_derive_returns_none_when_no_ratings(self):
        txn = self._make_txn()
        result = derive_feedback_fields(txn)
        self.assertIsNone(result["feedback_value_score"])
        self.assertIsNone(result["feedback_confidence"])

    def test_derive_produces_score_with_all_three_ratings(self):
        txn = self._make_txn(
            satisfaction_rating=8,
            regret_rating=20,
            repurchase_likelihood=70,
        )
        result = derive_feedback_fields(txn)
        self.assertIsNotNone(result["feedback_value_score"])
        self.assertIsNotNone(result["feedback_confidence"])
        self.assertGreater(result["feedback_value_score"], 0)
        self.assertLessEqual(result["feedback_value_score"], 1.0)
        # Full confidence when all three are present
        self.assertAlmostEqual(result["feedback_confidence"], 1.0, places=1)

    def test_derive_partial_ratings_give_lower_confidence(self):
        txn_full = self._make_txn(
            satisfaction_rating=7, regret_rating=30, repurchase_likelihood=60,
        )
        txn_partial = self._make_txn(satisfaction_rating=7)
        full = derive_feedback_fields(txn_full)
        partial = derive_feedback_fields(txn_partial)
        self.assertLess(partial["feedback_confidence"], full["feedback_confidence"])

    def test_reflection_text_boosts_confidence(self):
        txn_no_text = self._make_txn(satisfaction_rating=7, regret_rating=30)
        txn_with_text = self._make_txn(
            satisfaction_rating=7, regret_rating=30, reflection_text="Great value"
        )
        no_text = derive_feedback_fields(txn_no_text)
        with_text = derive_feedback_fields(txn_with_text)
        self.assertGreaterEqual(with_text["feedback_confidence"], no_text["feedback_confidence"])

    def test_apply_feedback_scoring_persists_fields(self):
        txn = self._make_txn(satisfaction_rating=9, regret_rating=10, repurchase_likelihood=90)
        apply_feedback_scoring(txn, save=True)
        txn.refresh_from_db()
        self.assertIsNotNone(txn.feedback_value_score)
        self.assertIsNotNone(txn.feedback_confidence)

    def test_apply_feedback_scoring_leaves_null_when_no_ratings(self):
        txn = self._make_txn()
        apply_feedback_scoring(txn, save=True)
        txn.refresh_from_db()
        self.assertIsNone(txn.feedback_value_score)
        self.assertIsNone(txn.feedback_confidence)

    def test_feedback_fields_not_writable_by_client(self):
        """Client-supplied feedback_value_score/impulse_score are ignored."""
        client = APIClient()
        client.force_authenticate(user=self.user)
        txn = self._make_txn()
        response = client.patch(
            f"/api/transactions/{txn.id}/",
            {
                "satisfaction_rating": 8,
                "feedback_value_score": 0.99,
                "impulse_score": 0.99,
                "regret_score": 0.99,
            },
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        txn.refresh_from_db()
        # backend derives from ratings; should NOT be the client-supplied value
        self.assertNotEqual(txn.feedback_value_score, 0.99)
        self.assertIsNone(txn.impulse_score)
        self.assertIsNone(txn.regret_score)


class ConsideredAtSanitizationTestCase(TestCase):
    """Tests for considered_at timing hygiene."""

    def setUp(self):
        self.user = User.objects.create_user(
            username="considered-at-user",
            email="cat@example.com",
            password="testpass123",
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    def test_considered_at_after_occurred_at_is_discarded(self):
        """considered_at >= occurred_at should be nulled (feedback-submission time)."""
        occurred = timezone.now() - timedelta(days=3)
        txn = Transaction.objects.create(
            user=self.user,
            direction="spend",
            amount=30,
            occurred_at=occurred,
            category=TransactionCategory.SHOPPING,
        )
        # Simulate frontend sending 'now' as considered_at (wrong behaviour).
        bogus_considered_at = timezone.now().isoformat()
        response = self.client.patch(
            f"/api/transactions/{txn.id}/",
            {"considered_at": bogus_considered_at},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        txn.refresh_from_db()
        self.assertIsNone(txn.considered_at)

    def test_valid_considered_at_before_occurred_is_accepted(self):
        """A considered_at genuinely before occurred_at should be stored."""
        occurred = timezone.now() - timedelta(days=3)
        valid_considered = (occurred - timedelta(minutes=30)).isoformat()
        txn = Transaction.objects.create(
            user=self.user,
            direction="spend",
            amount=30,
            occurred_at=occurred,
            category=TransactionCategory.SHOPPING,
        )
        response = self.client.patch(
            f"/api/transactions/{txn.id}/",
            {"considered_at": valid_considered},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        txn.refresh_from_db()
        self.assertIsNotNone(txn.considered_at)


class MerchantNormalizationTestCase(TestCase):
    """Tests for merchant name normalisation reducing fragmentation."""

    def setUp(self):
        from apps.subscriptions.models import Merchant
        self.Merchant = Merchant

    def test_normalize_merchant_name_strips_trailing_store_number(self):
        from apps.valuations.services.transaction_value_score import _normalize_merchant_name
        self.assertEqual(_normalize_merchant_name("CVS Pharmacy #1234"), "cvs pharmacy")
        self.assertEqual(_normalize_merchant_name("CVS Pharmacy"), "cvs pharmacy")

    def test_normalize_merchant_name_collapses_whitespace(self):
        from apps.valuations.services.transaction_value_score import _normalize_merchant_name
        self.assertEqual(_normalize_merchant_name("  Walgreens  "), "walgreens")

    def test_normalize_merchant_name_handles_empty(self):
        from apps.valuations.services.transaction_value_score import _normalize_merchant_name
        self.assertEqual(_normalize_merchant_name(""), "")
        self.assertEqual(_normalize_merchant_name(None), "")


class HealthCategoryMappingTestCase(TestCase):
    """Tests for health/pharmacy category preservation through the taxonomy."""

    def test_health_category_maps_to_health_merchant_category(self):
        from apps.valuations.services.transaction_value_score import _merchant_category_from_transaction
        from apps.subscriptions.models import MerchantCategory
        result = _merchant_category_from_transaction(TransactionCategory.HEALTH)
        self.assertEqual(result, MerchantCategory.HEALTH)

    def test_health_merchant_category_maps_to_other_at_model_boundary(self):
        """model-safe mapping preserves backward compatibility for the model vocab."""
        from apps.valuations.services.transaction_value_score import _model_safe_merchant_category
        result = _model_safe_merchant_category("health")
        self.assertEqual(result, "other")

    def test_model_safe_passes_through_known_vocab(self):
        from apps.valuations.services.transaction_value_score import _model_safe_merchant_category
        for cat in ["streaming", "grocery", "fitness", "software", "utilities", "food", "education", "other"]:
            self.assertEqual(_model_safe_merchant_category(cat), cat)


class UsageFrequencyValidationTestCase(TestCase):
    """Tests for usage_frequency sanitization."""

    def setUp(self):
        self.user = User.objects.create_user(
            username="uf-user",
            email="uf@example.com",
            password="testpass123",
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    def _make_txn(self):
        return Transaction.objects.create(
            user=self.user,
            direction="spend",
            amount=40,
            occurred_at=timezone.now(),
            category=TransactionCategory.SHOPPING,
        )

    def test_positive_usage_frequency_is_stored(self):
        txn = self._make_txn()
        response = self.client.patch(
            f"/api/transactions/{txn.id}/",
            {"satisfaction_rating": 7, "usage_frequency": 3},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        txn.refresh_from_db()
        self.assertEqual(txn.usage_frequency, 3)

    def test_zero_usage_frequency_coerced_to_null(self):
        """0 means unknown/error; backend converts to None."""
        txn = self._make_txn()
        response = self.client.patch(
            f"/api/transactions/{txn.id}/",
            {"satisfaction_rating": 7, "usage_frequency": 0},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        txn.refresh_from_db()
        self.assertIsNone(txn.usage_frequency)

    def test_negative_usage_frequency_coerced_to_null(self):
        txn = self._make_txn()
        response = self.client.patch(
            f"/api/transactions/{txn.id}/",
            {"satisfaction_rating": 7, "usage_frequency": -1},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        txn.refresh_from_db()
        self.assertIsNone(txn.usage_frequency)

    def test_excessive_usage_frequency_rejected(self):
        txn = self._make_txn()
        response = self.client.patch(
            f"/api/transactions/{txn.id}/",
            {"satisfaction_rating": 7, "usage_frequency": 999},
            format="json",
        )
        self.assertEqual(response.status_code, 400)
