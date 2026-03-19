from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from ai.intents import classify_intent
from ai.models import Conversation, ConversationContext, MessageRole
from ai.views import SAFETY_GUARDRAILS, build_assistant_placeholder_response, build_financial_context, build_purchase_advisor_report
from subscriptions.models import BillingCycle, Merchant, Subscription, SubscriptionStatus
from transactions.models import Transaction, TransactionCategory, TransactionDirection
from users.models import User, UserComputed, UserRawExplicit


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
        self.assertIn("intent_detection", assistant_message["metadata_json"])
        self.assertEqual(assistant_message["metadata_json"]["intent_detection"]["intent"], "ask")
        self.assertEqual(assistant_message["metadata_json"]["response_style"], "direct_answer")
        self.assertIn("question", assistant_message["content"].lower())

        financial_context = assistant_message["metadata_json"]["financial_context"]
        self.assertEqual(financial_context["summary"]["transaction_count"], 1)
        self.assertEqual(financial_context["summary"]["active_subscription_count"], 1)
        self.assertEqual(assistant_message["metadata_json"]["safety_guardrails"], SAFETY_GUARDRAILS)


class IntentClassificationTests(TestCase):
    def test_classify_intent_supports_requested_labels(self):
        self.assertEqual(classify_intent("Can you help me budget?")["intent"], "ask")
        self.assertEqual(classify_intent("Please rewrite this response in a friendly tone.")["intent"], "edit")
        self.assertEqual(classify_intent("What do you recommend I cut first?")["intent"], "recommend")
        self.assertEqual(classify_intent("Summarize this chat into 3 bullets.")["intent"], "summarize")
        self.assertEqual(classify_intent("I bought shoes for $80 today")["intent"], "record_transaction")


class AssistantPlaceholderResponseTests(TestCase):
    def test_placeholder_response_changes_by_intent(self):
        expected_styles = {
            "ask": "direct_answer",
            "edit": "transformation",
            "recommend": "ranked_recommendations",
            "summarize": "summary",
        }

        for intent, expected_style in expected_styles.items():
            with self.subTest(intent=intent):
                payload = build_assistant_placeholder_response(intent, openai_configured=False)
                self.assertEqual(payload["response_style"], expected_style)
                self.assertTrue(payload["assistant_text"].startswith("✅"))
                self.assertIn("OpenAI key is not configured yet", payload["assistant_text"])
                self.assertIn(SAFETY_GUARDRAILS["disclaimer"], payload["assistant_text"])
                self.assertEqual(payload["safety_guardrails"], SAFETY_GUARDRAILS)


class ChatNavigationOptionsTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(username="seed_user_1", password="testpass")
        self.conversation = Conversation.objects.create(
            user=self.user,
            context_type=ConversationContext.BUDGETING,
        )

    def test_purchase_message_returns_navigation_options_without_db_write(self):
        response = self.client.post(
            f"/api/ai/conversations/{self.conversation.id}/messages/",
            {"content": "I bought coffee at Starbucks for $6.50 today"},
            format="json",
            HTTP_X_DEV_USER="seed_user_1",
        )

        self.assertEqual(response.status_code, 201)

        assistant_message = response.data["assistant_message"]
        metadata = assistant_message["metadata_json"]

        self.assertEqual(metadata["intent_detection"]["intent"], "record_transaction")
        self.assertEqual(metadata["action"], "navigate_to_data_entry")
        self.assertEqual(metadata["action_status"], "routing_options")
        self.assertIsNone(metadata["created_transaction_id"])
        self.assertGreaterEqual(len(metadata["quick_actions"]), 3)
        self.assertEqual(metadata["safety_guardrails"], SAFETY_GUARDRAILS)
        self.assertEqual(Transaction.objects.filter(user=self.user).count(), 0)


class PurchaseAdvisorReportTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="advisor_user", password="testpass")
        merchant = Merchant.objects.create(name="Starbucks")
        now = timezone.now()

        for days_ago, amount, category in [
            (1, Decimal("28.00"), TransactionCategory.EATING_OUT),
            (2, Decimal("22.00"), TransactionCategory.EATING_OUT),
            (3, Decimal("15.00"), TransactionCategory.GROCERIES),
        ]:
            Transaction.objects.create(
                user=self.user,
                merchant=merchant,
                amount=amount,
                currency="USD",
                direction=TransactionDirection.SPEND,
                occurred_at=now - timedelta(days=days_ago),
                category=category,
                description_raw="Test transaction",
            )

        UserRawExplicit.objects.create(
            user=self.user,
            life_stage="early_career",
            financial_goal="save_more",
            budget_style="strict",
        )
        UserComputed.objects.create(
            user=self.user,
            spending_personality="Value Hunter",
            budget_adherence_score=0.82,
        )

        self.user.preferences.create(
            key="purchase_advisor_logic",
            value_type="json",
            value_json={
                "enabled": True,
                "lookback_days": 30,
                "overspending_ratio_threshold": 1.2,
                "focus_categories": ["food", "grocery"],
            },
        )

    def test_build_purchase_advisor_report_flags_requested_category_overspending(self):
        report = build_purchase_advisor_report(self.user, "Can you review my food spending?")

        self.assertEqual(report["status"], "ready")
        self.assertEqual(report["requested_category"], "food")
        self.assertTrue(report["targeted_report"]["is_overspending"])
        self.assertEqual(report["overspending_categories"][0]["category"], "food")
        self.assertEqual(report["profile_context"]["life_stage"], "early_career")
        self.assertEqual(report["profile_context"]["financial_goal"], "save_more")


class PurchaseAdvisorChatMetadataTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(username="seed_user_2", password="testpass")
        self.conversation = Conversation.objects.create(user=self.user, context_type=ConversationContext.BUDGETING)
        merchant = Merchant.objects.create(name="Trader Joe's")
        now = timezone.now()

        self.user.preferences.create(
            key="purchase_advisor_logic",
            value_type="json",
            value_json={"enabled": True, "lookback_days": 30, "overspending_ratio_threshold": 1.1},
        )

        Transaction.objects.create(
            user=self.user,
            merchant=merchant,
            amount=Decimal("120.00"),
            currency="USD",
            direction=TransactionDirection.SPEND,
            occurred_at=now - timedelta(days=1),
            category=TransactionCategory.GROCERIES,
            description_raw="Weekly groceries",
        )
        Transaction.objects.create(
            user=self.user,
            merchant=merchant,
            amount=Decimal("30.00"),
            currency="USD",
            direction=TransactionDirection.SPEND,
            occurred_at=now - timedelta(days=2),
            category=TransactionCategory.EATING_OUT,
            description_raw="Lunch",
        )

    def test_messages_post_includes_purchase_advisor_report_when_enabled(self):
        response = self.client.post(
            f"/api/ai/conversations/{self.conversation.id}/messages/",
            {"content": "Am I overspending on grocery right now?"},
            format="json",
            HTTP_X_DEV_USER="seed_user_2",
        )

        self.assertEqual(response.status_code, 201)
        report = response.data["assistant_message"]["metadata_json"]["purchase_advisor_report"]
        self.assertEqual(report["status"], "ready")
        self.assertEqual(report["requested_category"], "grocery")
        self.assertTrue(report["targeted_report"]["is_overspending"])
