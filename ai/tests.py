from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from ai.models import Conversation, ConversationContext, MessageRole
from ai.views import build_financial_context
from subscriptions.models import BillingCycle, Merchant, Subscription, SubscriptionStatus
from transactions.models import Transaction, TransactionCategory, TransactionDirection
from users.models import User


class FinancialContextTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="ctx_user", password="testpass")
        self.other_user = User.objects.create_user(username="other_user", password="testpass")

        self.netflix = Merchant.objects.create(name="Netflix")
        self.spotify = Merchant.objects.create(name="Spotify")
        self.prime = Merchant.objects.create(name="Prime Video")

        now = timezone.now()

        Transaction.objects.create(
            user=self.user,
            merchant=self.netflix,
            amount=Decimal("19.99"),
            currency="USD",
            direction=TransactionDirection.SPEND,
            occurred_at=now - timedelta(days=1),
            category=TransactionCategory.SUBSCRIPTIONS,
            description_raw="NETFLIX.COM",
        )
        Transaction.objects.create(
            user=self.user,
            merchant=self.spotify,
            amount=Decimal("12.00"),
            currency="USD",
            direction=TransactionDirection.SPEND,
            occurred_at=now - timedelta(days=2),
            category=TransactionCategory.SUBSCRIPTIONS,
            description_raw="SPOTIFY",
        )
        Transaction.objects.create(
            user=self.user,
            merchant=self.prime,
            amount=Decimal("5.00"),
            currency="USD",
            direction=TransactionDirection.REFUND,
            occurred_at=now - timedelta(days=3),
            category=TransactionCategory.ENTERTAINMENT,
            description_raw="AMZN REFUND",
        )
        Transaction.objects.create(
            user=self.other_user,
            amount=Decimal("50.00"),
            currency="USD",
            direction=TransactionDirection.SPEND,
            occurred_at=now,
            category=TransactionCategory.OTHER,
            description_raw="OTHER USER TX",
        )

        Subscription.objects.create(
            user=self.user,
            merchant=self.netflix,
            status=SubscriptionStatus.ACTIVE,
            billing_cycle=BillingCycle.MONTHLY,
            price=Decimal("15.99"),
            currency="USD",
        )
        Subscription.objects.create(
            user=self.user,
            merchant=self.spotify,
            status=SubscriptionStatus.CANCELED,
            billing_cycle=BillingCycle.MONTHLY,
            price=Decimal("9.99"),
            currency="USD",
        )

    def test_build_financial_context_returns_recent_user_data(self):
        context = build_financial_context(self.user)

        self.assertEqual(context["summary"]["transaction_count"], 3)
        self.assertEqual(context["summary"]["active_subscription_count"], 1)
        self.assertEqual(context["summary"]["recent_spend_total"], "31.99")
        self.assertEqual(len(context["recent_transactions"]), 3)
        self.assertEqual(context["recent_transactions"][0]["merchant"], "Netflix")
        self.assertEqual(len(context["active_subscriptions"]), 1)
        self.assertEqual(context["active_subscriptions"][0]["merchant"], "Netflix")


class ConversationMessagesTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(username="seed_user_0", password="testpass")

        merchant = Merchant.objects.create(name="YouTube Premium")
        Transaction.objects.create(
            user=self.user,
            merchant=merchant,
            amount=Decimal("13.99"),
            currency="USD",
            direction=TransactionDirection.SPEND,
            occurred_at=timezone.now(),
            category=TransactionCategory.SUBSCRIPTIONS,
            description_raw="YOUTUBE",
        )
        Subscription.objects.create(
            user=self.user,
            merchant=merchant,
            status=SubscriptionStatus.ACTIVE,
            billing_cycle=BillingCycle.MONTHLY,
            price=Decimal("13.99"),
            currency="USD",
        )

        self.conversation = Conversation.objects.create(
            user=self.user,
            context_type=ConversationContext.GENERAL,
        )

    def test_messages_post_includes_financial_context_in_assistant_metadata(self):
        response = self.client.post(
            f"/api/ai/conversations/{self.conversation.id}/messages/",
            {"content": "Can you help me with my budget?"},
            format="json",
            HTTP_X_DEV_USER="seed_user_0",
        )

        self.assertEqual(response.status_code, 201)

        assistant_message = response.data["assistant_message"]
        self.assertEqual(assistant_message["role"], MessageRole.ASSISTANT)
        self.assertIn("financial_context", assistant_message["metadata_json"])

        financial_context = assistant_message["metadata_json"]["financial_context"]
        self.assertEqual(financial_context["summary"]["transaction_count"], 1)
        self.assertEqual(financial_context["summary"]["active_subscription_count"], 1)
