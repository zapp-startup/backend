from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from transactions.feedback_candidates import (
    _category_weight,
    _recency_score,
    get_feedback_candidates,
)
from transactions.models import Transaction, TransactionCategory
from users.models import User


class FeedbackCandidatesTestCase(TestCase):
    """Tests for feedback candidate selection."""

    def setUp(self):
        self.user = User.objects.create_user(
            username="feedback-user",
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
