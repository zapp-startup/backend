import json
import requests
from datetime import datetime, timedelta
from decimal import Decimal
from unittest.mock import Mock, patch

from django.core.cache import cache
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.test import APIClient

from apps.ai.intents import classify_intent
from apps.ai.models import Conversation, ConversationContext, ConversationMemoryItem, MessageRole, Message, UserFact
from apps.ai.purchase_advisor import extract_requested_category
from apps.ai.views import SAFETY_GUARDRAILS, build_assistant_placeholder_response, build_financial_context, build_purchase_advisor_report
from apps.subscriptions.models import BillingCycle, Merchant, Subscription, SubscriptionStatus
from apps.transactions.models import Transaction, TransactionCategory, TransactionDirection
from apps.users.models import User, UserComputed, UserRawExplicit


class FinancialContextTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="ctx_user", password="testpass")
        self.other_user = User.objects.create_user(username="other_user", password="testpass")

        self.netflix = Merchant.objects.create(name="Netflix")
        self.spotify = Merchant.objects.create(name="Spotify")
        self.prime = Merchant.objects.create(name="Prime Video")

        self.now = timezone.now().replace(day=15, hour=12, minute=0, second=0, microsecond=0)

        Transaction.objects.create(
            user=self.user,
            merchant=self.netflix,
            amount=Decimal("19.99"),
            currency="USD",
            direction=TransactionDirection.SPEND,
            occurred_at=self.now - timedelta(days=1),
            category=TransactionCategory.SUBSCRIPTIONS,
            description_raw="NETFLIX.COM",
        )
        Transaction.objects.create(
            user=self.user,
            merchant=self.spotify,
            amount=Decimal("12.00"),
            currency="USD",
            direction=TransactionDirection.SPEND,
            occurred_at=self.now - timedelta(days=2),
            category=TransactionCategory.SUBSCRIPTIONS,
            description_raw="SPOTIFY",
        )
        Transaction.objects.create(
            user=self.user,
            merchant=self.prime,
            amount=Decimal("5.00"),
            currency="USD",
            direction=TransactionDirection.REFUND,
            occurred_at=self.now - timedelta(days=3),
            category=TransactionCategory.ENTERTAINMENT,
            description_raw="AMZN REFUND",
        )
        Transaction.objects.create(
            user=self.other_user,
            amount=Decimal("50.00"),
            currency="USD",
            direction=TransactionDirection.SPEND,
            occurred_at=self.now,
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
        with patch("apps.ai.views.timezone.now", return_value=self.now):
            context = build_financial_context(self.user)

        self.assertEqual(context["summary"]["transaction_count"], 3)
        self.assertEqual(context["summary"]["active_subscription_count"], 1)
        self.assertEqual(context["summary"]["recent_spend_total"], "31.99")
        self.assertEqual(context["summary"]["spend_last_30_days"], "31.99")
        self.assertEqual(context["summary"]["spend_transaction_count_last_30_days"], 2)
        self.assertEqual(context["summary"]["spend_month_to_date"], "31.99")
        self.assertEqual(context["summary"]["spend_transaction_count_month_to_date"], 2)
        self.assertEqual(context["summary"]["active_subscription_monthly_commitment"], "15.99")
        self.assertEqual(len(context["recent_transactions"]), 3)
        self.assertEqual(context["recent_transactions"][0]["merchant"], "Netflix")
        self.assertEqual(len(context["active_subscriptions"]), 1)
        self.assertEqual(context["active_subscriptions"][0]["merchant"], "Netflix")
        self.assertEqual(context["spending_by_category_30d"][0]["category"], TransactionCategory.SUBSCRIPTIONS)
        self.assertEqual(context["spending_by_category_30d"][0]["amount"], "31.99")

    def test_build_financial_context_includes_requested_spend_window_summary(self):
        Transaction.objects.filter(user=self.user).delete()

        Transaction.objects.create(
            user=self.user,
            merchant=self.netflix,
            amount=Decimal("7.00"),
            currency="USD",
            direction=TransactionDirection.SPEND,
            occurred_at=self.now - timedelta(days=1),
            category=TransactionCategory.ENTERTAINMENT,
            description_raw="RECENT WINDOW SPEND 1",
        )
        Transaction.objects.create(
            user=self.user,
            merchant=self.spotify,
            amount=Decimal("8.00"),
            currency="USD",
            direction=TransactionDirection.SPEND,
            occurred_at=self.now - timedelta(hours=47),
            category=TransactionCategory.SHOPPING,
            description_raw="RECENT WINDOW SPEND 2",
        )
        Transaction.objects.create(
            user=self.user,
            merchant=self.prime,
            amount=Decimal("9.00"),
            currency="USD",
            direction=TransactionDirection.SPEND,
            occurred_at=self.now - timedelta(days=12),
            category=TransactionCategory.ENTERTAINMENT,
            description_raw="OLDER SPEND",
        )

        with patch("apps.ai.views.timezone.now", return_value=self.now):
            context = build_financial_context(self.user, requested_spend_window_days=2)

        self.assertEqual(context["summary"]["requested_spend_window_days"], 2)
        self.assertEqual(context["summary"]["spend_in_requested_window"], "15.00")
        self.assertEqual(context["summary"]["spend_transaction_count_in_requested_window"], 2)


class ConversationMessagesTests(TestCase):
    def setUp(self):
        cache.clear()
        self.client = APIClient()
        self.user = User.objects.create_user(username="seed_user_0", password="testpass")
        self.client.force_authenticate(user=self.user)
        self.fixed_now = timezone.make_aware(datetime(2026, 3, 28, 12, 0, 0))

        merchant = Merchant.objects.create(name="YouTube Premium")
        Transaction.objects.create(
            user=self.user,
            merchant=merchant,
            amount=Decimal("13.99"),
            currency="USD",
            direction=TransactionDirection.SPEND,
            occurred_at=self.fixed_now,
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

    def test_messages_post_returns_backend_conversation_memory_snapshot(self):
        response = self.client.post(
            f"/api/ai/conversations/{self.conversation.id}/messages/",
            {"content": "Can you remind me about my subscription budget?"},
            format="json",
        )

        self.assertEqual(response.status_code, 201)

        assistant_message = response.data["assistant_message"]
        conversation_memory = assistant_message["metadata_json"]["conversation_memory"]
        self.assertEqual(conversation_memory["summary_text"], "")
        self.assertEqual(conversation_memory["session_state"]["active_goal"], "general_guidance")
        self.assertIn("budget", conversation_memory["session_state"]["mentioned_entities"])
        self.assertEqual(len(conversation_memory["recent_messages"]), 2)
        self.assertEqual(conversation_memory["recent_messages"][0]["role"], MessageRole.USER)
        self.assertEqual(conversation_memory["recent_messages"][1]["role"], MessageRole.ASSISTANT)
        self.assertEqual(conversation_memory["recent_messages"][1]["content"], assistant_message["content"])
        self.assertEqual(conversation_memory["session_state"]["message_count"], 2)

        self.conversation.refresh_from_db()
        self.assertEqual(self.conversation.session_state_json["active_goal"], "general_guidance")
        self.assertEqual(self.conversation.session_state_json["last_user_message"], "Can you remind me about my subscription budget?")

    def test_messages_post_compacts_older_turns_into_summary(self):
        for index in range(10):
            self.client.post(
                f"/api/ai/conversations/{self.conversation.id}/messages/",
                {"content": f"Help me track budget item {index}?"},
                format="json",
            )

        self.conversation.refresh_from_db()
        self.assertTrue(self.conversation.summary_text.startswith("User: Help me track budget item 0?"))
        self.assertIn("User: Help me track budget item 6?", self.conversation.summary_text)
        self.assertIsNotNone(self.conversation.last_summarized_message_id)
        self.assertEqual(self.conversation.session_state_json["active_goal"], "general_guidance")
        self.assertLessEqual(len(self.conversation.session_state_json["open_loops"]), 3)

    @patch("apps.ai.views.requests.post")
    @override_settings(OPENAI_API_KEY="test-key")
    def test_messages_post_uses_openai_when_configured(self, mock_post):
        mock_response = Mock()
        mock_response.json.return_value = {
            "choices": [
                {
                    "message": {
                        "content": (
                            '{"response_style":"direct_answer",'
                            '"message":"Based on the recent transactions I can see, you have spent $13.99 recently.",'
                            '"follow_up_question":"What monthly income should I compare that against?",'
                            '"disclaimer":null,'
                            '"safe_bounds_acknowledged":true}'
                        )
                    }
                }
            ]
        }
        mock_response.raise_for_status.return_value = None
        mock_post.return_value = mock_response

        response = self.client.post(
            f"/api/ai/conversations/{self.conversation.id}/messages/",
            {"content": "Can you help me optimize spending?"},
            format="json",
        )

        self.assertEqual(response.status_code, 201)
        assistant_message = response.data["assistant_message"]
        self.assertEqual(assistant_message["metadata_json"]["mode"], "openai")
        self.assertEqual(assistant_message["metadata_json"]["response_style"], "direct_answer")
        self.assertIn("Based on the recent transactions I can see", assistant_message["content"])
        self.assertIn("What monthly income should I compare that against?", assistant_message["content"])
        self.assertNotIn("Recommendation:", assistant_message["content"])
        self.assertNotIn("Confidence:", assistant_message["content"])
        self.assertIn("model_response_json", assistant_message["metadata_json"])

    @patch("apps.ai.views.requests.post")
    @override_settings(OPENAI_API_KEY="test-key", OPENAI_MODEL="gpt-test-model")
    def test_openai_payload_includes_conversation_memory_and_user_facts(self, mock_post):
        UserFact.objects.create(
            user=self.user,
            fact_key="budget_focus",
            fact_value_json={"value": "subscriptions"},
            confidence=Decimal("0.90"),
        )
        Message.objects.create(
            conversation=self.conversation,
            role=MessageRole.USER,
            content="Earlier budget question",
        )
        Message.objects.create(
            conversation=self.conversation,
            role=MessageRole.ASSISTANT,
            content="Earlier budget answer",
        )

        mock_response = Mock()
        mock_response.json.return_value = {
            "choices": [
                {
                    "message": {
                        "content": (
                            '{"response_style":"direct_answer",'
                            '"message":"Based on the recent transactions I can see, you have spent $13.99 recently.",'
                            '"follow_up_question":null,'
                            '"disclaimer":null,'
                            '"safe_bounds_acknowledged":true}'
                        )
                    }
                }
            ]
        }
        mock_response.raise_for_status.return_value = None
        mock_post.return_value = mock_response

        response = self.client.post(
            f"/api/ai/conversations/{self.conversation.id}/messages/",
            {"content": "Can you help me optimize spending?"},
            format="json",
        )

        self.assertEqual(response.status_code, 201)
        request_json = mock_post.call_args.kwargs["json"]
        payload = json.loads(request_json["messages"][1]["content"])

        self.assertEqual(request_json["model"], "gpt-test-model")
        self.assertEqual(payload["request"]["intent"], "ask")
        self.assertEqual(payload["request"]["response_style"], "direct_answer")
        self.assertEqual(
            payload["conversation"]["memory"]["session_state"]["last_user_message"],
            "Can you help me optimize spending?",
        )
        self.assertEqual(
            payload["conversation"]["memory"]["recent_messages"][-1]["content"],
            "Can you help me optimize spending?",
        )
        self.assertEqual(payload["user_facts"][0]["key"], "budget_focus")
        self.assertIsNone(payload["purchase_advisor_report"])
        self.assertEqual(payload["spending"]["summary"]["spend_last_30_days"], 13.99)
        self.assertEqual(payload["spending"]["summary"]["active_subscription_monthly_commitment"], 13.99)

    @patch("apps.ai.views.requests.post")
    @override_settings(OPENAI_API_KEY="test-key", OPENAI_MODEL="gpt-test-model")
    def test_openai_payload_includes_purchase_advisor_report_when_enabled(self, mock_post):
        self.user.preferences.create(
            key="purchase_advisor_logic",
            value_type="json",
            value_json={"enabled": True, "lookback_days": 30, "overspending_ratio_threshold": 1.1},
        )

        mock_response = Mock()
        mock_response.json.return_value = {
            "choices": [
                {
                    "message": {
                        "content": (
                            '{"response_style":"direct_answer",'
                            '"message":"Here is a direct answer.",'
                            '"follow_up_question":null,'
                            '"disclaimer":null,'
                            '"safe_bounds_acknowledged":true}'
                        )
                    }
                }
            ]
        }
        mock_response.raise_for_status.return_value = None
        mock_post.return_value = mock_response

        response = self.client.post(
            f"/api/ai/conversations/{self.conversation.id}/messages/",
            {"content": "Am I overspending on subscriptions right now?"},
            format="json",
        )

        self.assertEqual(response.status_code, 201)
        request_json = mock_post.call_args.kwargs["json"]
        payload = json.loads(request_json["messages"][1]["content"])

        self.assertIsNotNone(payload["purchase_advisor_report"])
        self.assertEqual(payload["purchase_advisor_report"]["status"], "ready")

    def test_messages_post_extracts_stated_monthly_income_into_memory(self):
        response = self.client.post(
            f"/api/ai/conversations/{self.conversation.id}/messages/",
            {"content": "My monthly income is $5000."},
            format="json",
        )

        self.assertEqual(response.status_code, 201)

        fact = UserFact.objects.get(user=self.user, fact_key="stated_monthly_income")
        self.assertEqual(fact.fact_value_json["value"], 5000.0)
        memory_item = ConversationMemoryItem.objects.get(
            user=self.user,
            dedupe_key="fact:stated_monthly_income",
        )
        self.assertEqual(memory_item.memory_kind, "fact")
        self.assertIn("monthly income", memory_item.summary_text.lower())

    def test_spending_advice_question_does_not_route_to_transaction_entry(self):
        response = self.client.post(
            f"/api/ai/conversations/{self.conversation.id}/messages/",
            {"content": "I spent too much this month, what should I do?"},
            format="json",
        )

        self.assertEqual(response.status_code, 201)
        assistant_message = response.data["assistant_message"]
        self.assertEqual(
            assistant_message["metadata_json"]["intent_detection"]["intent"],
            "recommend",
        )
        self.assertEqual(
            assistant_message["metadata_json"]["response_style"],
            "ranked_recommendations",
        )
        self.assertNotEqual(
            assistant_message["metadata_json"]["response_style"],
            "navigation_options",
        )
        self.assertNotIn(
            "Choose where you want to update your data",
            assistant_message["content"],
        )

    def test_change_question_stays_on_ask_path(self):
        response = self.client.post(
            f"/api/ai/conversations/{self.conversation.id}/messages/",
            {"content": "How will my subscription bill change next month?"},
            format="json",
        )

        self.assertEqual(response.status_code, 201)
        assistant_message = response.data["assistant_message"]
        self.assertEqual(
            assistant_message["metadata_json"]["intent_detection"]["intent"],
            "ask",
        )
        self.assertEqual(
            assistant_message["metadata_json"]["response_style"],
            "direct_answer",
        )

    @patch("apps.ai.views.requests.post")
    @override_settings(OPENAI_API_KEY="test-key")
    def test_openai_payload_includes_recalled_memories_from_prior_turns(self, mock_post):
        mock_response = Mock()
        mock_response.json.return_value = {
            "choices": [
                {
                    "message": {
                        "content": (
                            '{"response_style":"direct_answer",'
                            '"message":"Here is a direct answer.",'
                            '"follow_up_question":null,'
                            '"disclaimer":null,'
                            '"safe_bounds_acknowledged":true}'
                        )
                    }
                }
            ]
        }
        mock_response.raise_for_status.return_value = None
        mock_post.return_value = mock_response

        first_response = self.client.post(
            f"/api/ai/conversations/{self.conversation.id}/messages/",
            {"content": "Can you review my subscriptions?"},
            format="json",
        )
        self.assertEqual(first_response.status_code, 201)
        self.assertEqual(
            ConversationMemoryItem.objects.filter(user=self.user, memory_kind="topic").count(),
            1,
        )

        second_response = self.client.post(
            f"/api/ai/conversations/{self.conversation.id}/messages/",
            {"content": "What should I do about my subscriptions?"},
            format="json",
        )

        self.assertEqual(second_response.status_code, 201)
        second_request_json = mock_post.call_args.kwargs["json"]
        payload = json.loads(second_request_json["messages"][1]["content"])
        recalled_memories = payload["conversation"]["memory"]["recalled_memories"]

        self.assertGreaterEqual(len(recalled_memories), 1)
        self.assertIn("subscriptions", recalled_memories[0]["summary_text"].lower())
        self.assertIn(
            "retrieved_memories",
            second_response.data["assistant_message"]["metadata_json"],
        )
        self.assertGreaterEqual(
            len(second_response.data["assistant_message"]["metadata_json"]["retrieved_memories"]),
            1,
        )

    @patch("apps.ai.views.requests.post")
    @override_settings(OPENAI_API_KEY="test-key")
    def test_spend_window_question_uses_answer_flow_instead_of_transaction_routing(self, mock_post):
        fixed_now = timezone.make_aware(datetime(2026, 3, 28, 12, 0, 0))
        with patch("apps.ai.views.timezone.now", return_value=fixed_now):
            response = self.client.post(
                f"/api/ai/conversations/{self.conversation.id}/messages/",
                {"content": "How much have I spent in last 10 days?"},
                format="json",
            )

        self.assertEqual(response.status_code, 201)
        mock_post.assert_not_called()

        assistant_message = response.data["assistant_message"]
        self.assertEqual(response.data["assistant_message"]["metadata_json"]["intent_detection"]["intent"], "ask")
        self.assertNotIn("Choose where you want to update your data", assistant_message["content"])
        self.assertEqual(assistant_message["metadata_json"]["mode"], "router")
        self.assertIn("$13.99", assistant_message["content"])
        self.assertIn("last 10 days", assistant_message["content"].lower())
        self.conversation.refresh_from_db()
        self.assertEqual(
            self.conversation.session_state_json["last_spend_lookup"]["mode"],
            "rolling_window",
        )
        self.assertEqual(
            self.conversation.session_state_json["last_spend_lookup"]["date_from"],
            "2026-03-19",
        )
        self.assertEqual(
            self.conversation.session_state_json["last_spend_lookup"]["date_to"],
            "2026-03-28",
        )

    @patch("apps.ai.views.requests.post")
    @override_settings(OPENAI_API_KEY="test-key")
    def test_general_spend_question_uses_local_summary_response(self, mock_post):
        response = self.client.post(
            f"/api/ai/conversations/{self.conversation.id}/messages/",
            {"content": "How much am I spending?"},
            format="json",
        )

        self.assertEqual(response.status_code, 201)
        mock_post.assert_not_called()

        assistant_message = response.data["assistant_message"]
        self.assertEqual(assistant_message["metadata_json"]["intent_detection"]["intent"], "ask")
        self.assertEqual(assistant_message["metadata_json"]["mode"], "router")
        self.assertIn("$13.99", assistant_message["content"])
        self.assertIn("last 30 days", assistant_message["content"].lower())
        self.assertIn("month to date", assistant_message["content"].lower())

    @patch("apps.ai.views.requests.post")
    @override_settings(OPENAI_API_KEY="test-key")
    def test_smalltalk_bypasses_openai_and_returns_standard_response(self, mock_post):
        response = self.client.post(
            f"/api/ai/conversations/{self.conversation.id}/messages/",
            {"content": "hi"},
            format="json",
        )

        self.assertEqual(response.status_code, 201)
        mock_post.assert_not_called()

        assistant_message = response.data["assistant_message"]
        self.assertEqual(assistant_message["metadata_json"]["intent_detection"]["intent"], "smalltalk")
        self.assertEqual(assistant_message["metadata_json"]["mode"], "router")
        self.assertEqual(assistant_message["metadata_json"]["response_style"], "direct_answer")
        self.assertIn("Hi. I can help with spending, subscriptions, and budgeting.", assistant_message["content"])
        self.assertEqual(assistant_message["metadata_json"]["financial_context"], {})

    @patch("apps.ai.views.requests.post")
    @override_settings(OPENAI_API_KEY="test-key")
    def test_meta_help_bypasses_openai_and_returns_capability_response(self, mock_post):
        response = self.client.post(
            f"/api/ai/conversations/{self.conversation.id}/messages/",
            {"content": "what can you do?"},
            format="json",
        )

        self.assertEqual(response.status_code, 201)
        mock_post.assert_not_called()

        assistant_message = response.data["assistant_message"]
        self.assertEqual(assistant_message["metadata_json"]["intent_detection"]["intent"], "meta_help")
        self.assertEqual(assistant_message["metadata_json"]["mode"], "router")
        self.assertEqual(assistant_message["metadata_json"]["response_style"], "direct_answer")
        self.assertIn("I can answer questions about your spending, subscriptions, and recent activity", assistant_message["content"])
        self.assertEqual(assistant_message["metadata_json"]["financial_context"], {})

    @patch("apps.ai.views.requests.post", side_effect=requests.RequestException("boom"))
    @override_settings(OPENAI_API_KEY="test-key")
    def test_openai_failure_returns_runtime_fallback_message(self, mock_post):
        response = self.client.post(
            f"/api/ai/conversations/{self.conversation.id}/messages/",
            {"content": "Can you help me optimize spending?"},
            format="json",
        )

        self.assertEqual(response.status_code, 201)
        assistant_message = response.data["assistant_message"]
        self.assertTrue(assistant_message["metadata_json"]["openai_configured"])
        self.assertIn("temporarily unavailable", assistant_message["content"])
        self.assertNotIn("not configured yet", assistant_message["content"])

    @patch("apps.ai.views.requests.post")
    @override_settings(OPENAI_API_KEY="test-key")
    def test_openai_401_returns_auth_specific_fallback_message(self, mock_post):
        mock_response = Mock(status_code=401)
        mock_response.raise_for_status.side_effect = requests.HTTPError("401 Client Error", response=mock_response)
        mock_post.return_value = mock_response

        response = self.client.post(
            f"/api/ai/conversations/{self.conversation.id}/messages/",
            {"content": "Can you help me optimize spending?"},
            format="json",
        )

        self.assertEqual(response.status_code, 201)
        assistant_message = response.data["assistant_message"]
        self.assertIn("rejected the server API key", assistant_message["content"])
        self.assertEqual(assistant_message["metadata_json"]["openai_error_code"], "auth_error")

    @patch("apps.ai.views.requests.post")
    @override_settings(OPENAI_API_KEY="sk-proj-testOPENAI_MODEL=gpt-4.1-mini")
    def test_malformed_openai_key_skips_request_and_returns_config_message(self, mock_post):
        response = self.client.post(
            f"/api/ai/conversations/{self.conversation.id}/messages/",
            {"content": "Can you help me optimize spending?"},
            format="json",
        )

        self.assertEqual(response.status_code, 201)
        mock_post.assert_not_called()
        assistant_message = response.data["assistant_message"]
        self.assertFalse(assistant_message["metadata_json"]["openai_configured"])
        self.assertEqual(assistant_message["metadata_json"]["openai_error_code"], "malformed_api_key")
        self.assertIn("looks malformed", assistant_message["content"])


class AIRateLimitingTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(username="seed_user_rate_limit", password="testpass")
        self.conversation = Conversation.objects.create(
            user=self.user,
            context_type=ConversationContext.GENERAL,
        )

    def tearDown(self):
        cache.clear()

    @override_settings(DEBUG=True)
    def test_ai_message_endpoint_returns_429_after_scope_limit_is_hit(self):
        cache.clear()
        original_rates = dict(ScopedRateThrottle.THROTTLE_RATES)
        self.addCleanup(setattr, ScopedRateThrottle, "THROTTLE_RATES", original_rates)
        ScopedRateThrottle.THROTTLE_RATES = {
            **ScopedRateThrottle.THROTTLE_RATES,
            "ai": "1/minute",
        }

        first_response = self.client.post(
            f"/api/ai/conversations/{self.conversation.id}/messages/",
            {"content": "Help me with my budget."},
            format="json",
            HTTP_X_DEV_USER="seed_user_rate_limit",
        )
        second_response = self.client.post(
            f"/api/ai/conversations/{self.conversation.id}/messages/",
            {"content": "Help me with my subscriptions."},
            format="json",
            HTTP_X_DEV_USER="seed_user_rate_limit",
        )

        self.assertEqual(first_response.status_code, 201)
        self.assertEqual(second_response.status_code, 429)


class IntentClassificationTests(TestCase):
    def test_classify_intent_supports_requested_labels(self):
        self.assertEqual(classify_intent("Can you help me budget?")["intent"], "ask")
        self.assertEqual(classify_intent("How much have I spent in last 10 days?")["intent"], "ask")
        self.assertEqual(classify_intent("How much am I spending?")["intent"], "ask")
        self.assertEqual(classify_intent("How much did I spend on March 12?")["intent"], "ask")
        self.assertEqual(classify_intent("How much did I spend from March 1 to March 7?")["intent"], "ask")
        self.assertEqual(classify_intent("No I just want to know how much I spent last week")["intent"], "ask")
        self.assertEqual(classify_intent("Set satisfaction for Starbucks to 8")["intent"], "update_satisfaction")
        self.assertEqual(classify_intent("hi")["intent"], "smalltalk")
        self.assertEqual(classify_intent("what can you do?")["intent"], "meta_help")
        self.assertEqual(classify_intent("Please rewrite this response in a friendly tone.")["intent"], "edit")
        self.assertEqual(classify_intent("What do you recommend I cut first?")["intent"], "recommend")
        self.assertEqual(classify_intent("Summarize this chat into 3 bullets.")["intent"], "summarize")
        self.assertEqual(classify_intent("I bought shoes for $80 today")["intent"], "record_transaction")

    def test_classify_intent_does_not_treat_generic_feedback_score_as_satisfaction_update(self):
        self.assertEqual(
            classify_intent("Set feedback score for Starbucks to 8")["intent"],
            "ask",
        )

    def test_classify_intent_does_not_treat_change_questions_as_edit_requests(self):
        self.assertEqual(
            classify_intent("How will my subscription bill change next month?")["intent"],
            "ask",
        )
        self.assertEqual(
            classify_intent("Can you update me on my spending?")["intent"],
            "ask",
        )

    def test_classify_intent_does_not_treat_advice_questions_as_transaction_logging(self):
        self.assertEqual(
            classify_intent("I spent too much this month, what should I do?")["intent"],
            "recommend",
        )
        self.assertEqual(
            classify_intent("I paid off my debt yesterday, what next?")["intent"],
            "ask",
        )

    def test_classify_intent_marks_smalltalk_variant(self):
        intent = classify_intent("thanks")

        self.assertEqual(intent["intent"], "smalltalk")
        self.assertEqual(intent["signals"]["smalltalk_variant"], "gratitude")

    def test_classify_intent_marks_spend_summary_queries(self):
        intent = classify_intent("How much have I spent in last 10 days?")

        self.assertEqual(intent["intent"], "ask")
        self.assertTrue(intent["signals"]["spend_summary_query"])

        general_intent = classify_intent("How much am I spending?")
        self.assertEqual(general_intent["intent"], "ask")
        self.assertTrue(general_intent["signals"]["spend_summary_query"])


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
        cache.clear()
        self.client = APIClient()
        self.user = User.objects.create_user(username="seed_user_1", password="testpass")
        self.client.force_authenticate(user=self.user)
        self.conversation = Conversation.objects.create(
            user=self.user,
            context_type=ConversationContext.BUDGETING,
        )

    def tearDown(self):
        cache.clear()

    def test_purchase_message_returns_navigation_options_without_db_write(self):
        response = self.client.post(
            f"/api/ai/conversations/{self.conversation.id}/messages/",
            {"content": "I bought coffee at Starbucks for $6.50 today"},
            format="json",
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


class SatisfactionUpdateFlowTests(TestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.client = APIClient()
        self.user = User.objects.create_user(username="satisfaction_user", password="testpass")
        self.other_user = User.objects.create_user(username="other_satisfaction_user", password="testpass")
        self.client.force_authenticate(user=self.user)
        self.fixed_now = timezone.make_aware(datetime(2026, 3, 28, 12, 0, 0))
        self.now_patcher = patch("apps.ai.views.timezone.now", return_value=self.fixed_now)
        self.now_patcher.start()
        self.addCleanup(self.now_patcher.stop)

        self.starbucks = Merchant.objects.create(name="Starbucks")
        self.uber = Merchant.objects.create(name="Uber")
        self.lyft = Merchant.objects.create(name="Lyft")
        self.target = Merchant.objects.create(name="Target")

        self.conversation = Conversation.objects.create(
            user=self.user,
            context_type=ConversationContext.GENERAL,
        )
        self.starbucks_purchase = Transaction.objects.create(
            user=self.user,
            merchant=self.starbucks,
            amount=Decimal("12.50"),
            currency="USD",
            direction=TransactionDirection.SPEND,
            occurred_at=self.fixed_now - timedelta(days=1),
            category=TransactionCategory.EATING_OUT,
            description_raw="STARBUCKS STORE 123",
        )
        self.uber_purchase = Transaction.objects.create(
            user=self.user,
            merchant=self.uber,
            amount=Decimal("18.25"),
            currency="USD",
            direction=TransactionDirection.SPEND,
            occurred_at=self.fixed_now - timedelta(days=1),
            category=TransactionCategory.TRANSPORT,
            description_raw="UBER TRIP",
        )

    def _post_message(self, content: str, *, action_payload: dict | None = None):
        payload = {"content": content}
        if action_payload is not None:
            payload["action_payload"] = action_payload
        return self.client.post(
            f"/api/ai/conversations/{self.conversation.id}/messages/",
            payload,
            format="json",
        )

    def test_single_match_creates_pending_confirmation_without_mutating(self):
        response = self._post_message("Set satisfaction for Starbucks to 8")

        self.assertEqual(response.status_code, 201)
        self.starbucks_purchase.refresh_from_db()
        self.assertIsNone(self.starbucks_purchase.satisfaction_rating)

        assistant_metadata = response.data["assistant_message"]["metadata_json"]
        self.assertEqual(assistant_metadata["action"], "update_satisfaction")
        self.assertEqual(assistant_metadata["action_status"], "needs_confirmation")
        self.assertEqual(
            [action["label"] for action in assistant_metadata["quick_actions"]],
            ["Confirm", "Cancel"],
        )

        self.conversation.refresh_from_db()
        pending_action = self.conversation.session_state_json["pending_action"]
        self.assertEqual(pending_action["kind"], "update_satisfaction")
        self.assertEqual(pending_action["transaction_id"], self.starbucks_purchase.id)
        self.assertEqual(pending_action["value"], 8)
        self.assertEqual(pending_action["status"], "needs_confirmation")

    def test_confirm_applies_satisfaction_rating_and_clears_pending_state(self):
        self._post_message("Set satisfaction for Starbucks to 8")

        confirm_response = self._post_message(
            "Confirm",
            action_payload={"kind": "confirm_pending_action"},
        )

        self.assertEqual(confirm_response.status_code, 201)
        self.starbucks_purchase.refresh_from_db()
        self.assertEqual(self.starbucks_purchase.satisfaction_rating, 8)

        self.conversation.refresh_from_db()
        self.assertNotIn("pending_action", self.conversation.session_state_json)
        self.assertEqual(
            self.conversation.session_state_json["pinned_transaction_id"],
            self.starbucks_purchase.id,
        )

    def test_action_only_confirm_does_not_pollute_memory_or_last_user_message(self):
        self._post_message("Set satisfaction for Starbucks to 8")

        confirm_response = self.client.post(
            f"/api/ai/conversations/{self.conversation.id}/messages/",
            {"action_payload": {"kind": "confirm_pending_action"}},
            format="json",
        )

        self.assertEqual(confirm_response.status_code, 201)
        self.assertEqual(confirm_response.data["user_message"]["content"], "")
        self.starbucks_purchase.refresh_from_db()
        self.assertEqual(self.starbucks_purchase.satisfaction_rating, 8)

        self.conversation.refresh_from_db()
        self.assertEqual(
            self.conversation.session_state_json["last_user_message"],
            "Set satisfaction for Starbucks to 8",
        )
        self.assertEqual(
            self.conversation.session_state_json["active_goal"],
            "transaction_feedback",
        )
        self.assertEqual(
            ConversationMemoryItem.objects.filter(user=self.user).count(),
            0,
        )

    def test_cancel_clears_pending_state_without_mutating(self):
        self._post_message("Set satisfaction for Starbucks to 8")

        cancel_response = self._post_message(
            "Cancel",
            action_payload={"kind": "cancel_pending_action"},
        )

        self.assertEqual(cancel_response.status_code, 201)
        self.starbucks_purchase.refresh_from_db()
        self.assertIsNone(self.starbucks_purchase.satisfaction_rating)

        self.conversation.refresh_from_db()
        self.assertNotIn("pending_action", self.conversation.session_state_json)

    def test_ambiguous_match_returns_candidate_buttons_and_supports_selection(self):
        second_starbucks_purchase = Transaction.objects.create(
            user=self.user,
            merchant=self.starbucks,
            amount=Decimal("6.25"),
            currency="USD",
            direction=TransactionDirection.SPEND,
            occurred_at=self.fixed_now - timedelta(days=2),
            category=TransactionCategory.EATING_OUT,
            description_raw="STARBUCKS DRIVE THRU",
        )

        response = self._post_message("Set satisfaction for Starbucks to 7")

        self.assertEqual(response.status_code, 201)
        assistant_metadata = response.data["assistant_message"]["metadata_json"]
        self.assertEqual(assistant_metadata["action_status"], "needs_target")
        self.assertEqual(len(assistant_metadata["quick_actions"]), 3)

        candidate_action = assistant_metadata["quick_actions"][0]
        select_response = self._post_message(
            candidate_action["label"],
            action_payload=candidate_action["action_payload"],
        )

        self.assertEqual(select_response.status_code, 201)
        select_metadata = select_response.data["assistant_message"]["metadata_json"]
        self.assertEqual(select_metadata["action_status"], "needs_confirmation")

        confirm_response = self._post_message(
            "Confirm",
            action_payload={"kind": "confirm_pending_action"},
        )
        self.assertEqual(confirm_response.status_code, 201)

        selected_transaction_id = candidate_action["action_payload"]["transaction_id"]
        self.starbucks_purchase.refresh_from_db()
        second_starbucks_purchase.refresh_from_db()
        selected_transaction = (
            self.starbucks_purchase
            if self.starbucks_purchase.id == selected_transaction_id
            else second_starbucks_purchase
        )
        unselected_transaction = (
            second_starbucks_purchase
            if selected_transaction is self.starbucks_purchase
            else self.starbucks_purchase
        )
        self.assertEqual(selected_transaction.satisfaction_rating, 7)
        self.assertIsNone(unselected_transaction.satisfaction_rating)

    def test_invalid_satisfaction_value_is_rejected(self):
        response = self._post_message("Set satisfaction for Starbucks to 11")

        self.assertEqual(response.status_code, 201)
        self.assertIn("1 to 10", response.data["assistant_message"]["content"])
        self.starbucks_purchase.refresh_from_db()
        self.assertIsNone(self.starbucks_purchase.satisfaction_rating)

        self.conversation.refresh_from_db()
        self.assertNotIn("pending_action", self.conversation.session_state_json)

    def test_feedback_score_request_requires_explicit_satisfaction_language(self):
        response = self._post_message("Set feedback score for Starbucks to 8")

        self.assertEqual(response.status_code, 201)
        self.assertIn("only update satisfaction", response.data["assistant_message"]["content"])
        self.starbucks_purchase.refresh_from_db()
        self.assertIsNone(self.starbucks_purchase.satisfaction_rating)

        self.conversation.refresh_from_db()
        self.assertNotIn("pending_action", self.conversation.session_state_json)

    def test_income_or_refund_transaction_cannot_be_updated(self):
        Transaction.objects.create(
            user=self.user,
            merchant=self.lyft,
            amount=Decimal("8.00"),
            currency="USD",
            direction=TransactionDirection.REFUND,
            occurred_at=self.fixed_now - timedelta(days=1),
            category=TransactionCategory.TRANSPORT,
            description_raw="LYFT REFUND",
        )

        response = self._post_message("Set satisfaction for Lyft to 6")

        self.assertEqual(response.status_code, 201)
        self.assertIn("only update satisfaction for spend transactions", response.data["assistant_message"]["content"])

    def test_cross_user_transaction_is_not_modified(self):
        other_target_transaction = Transaction.objects.create(
            user=self.other_user,
            merchant=self.target,
            amount=Decimal("42.00"),
            currency="USD",
            direction=TransactionDirection.SPEND,
            occurred_at=self.fixed_now - timedelta(days=1),
            category=TransactionCategory.SHOPPING,
            description_raw="TARGET RUN",
        )

        response = self._post_message("Set satisfaction for Target to 5")

        self.assertEqual(response.status_code, 201)
        self.assertIn("couldn't find that spend transaction", response.data["assistant_message"]["content"].lower())
        other_target_transaction.refresh_from_db()
        self.assertIsNone(other_target_transaction.satisfaction_rating)

    def test_explicit_date_can_resolve_transaction_older_than_lookback(self):
        old_transaction = Transaction.objects.create(
            user=self.user,
            merchant=self.target,
            amount=Decimal("55.00"),
            currency="USD",
            direction=TransactionDirection.SPEND,
            occurred_at=self.fixed_now - timedelta(days=120),
            category=TransactionCategory.SHOPPING,
            description_raw="TARGET OLDER PURCHASE",
        )

        response = self._post_message("Set satisfaction for Target on 2025-11-28 to 9")

        self.assertEqual(response.status_code, 201)
        self.conversation.refresh_from_db()
        pending_action = self.conversation.session_state_json["pending_action"]
        self.assertEqual(pending_action["transaction_id"], old_transaction.id)
        old_transaction.refresh_from_db()
        self.assertIsNone(old_transaction.satisfaction_rating)


class SpendLookupFlowTests(TestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.client = APIClient()
        self.user = User.objects.create_user(username="spend_lookup_user", password="testpass")
        self.client.force_authenticate(user=self.user)
        self.fixed_now = timezone.make_aware(datetime(2026, 3, 28, 12, 0, 0))
        self.now_patcher = patch("apps.ai.views.timezone.now", return_value=self.fixed_now)
        self.now_patcher.start()
        self.addCleanup(self.now_patcher.stop)

        self.grocer = Merchant.objects.create(name="Fresh Market")
        self.coffee = Merchant.objects.create(name="Coffee Bar")
        self.ride = Merchant.objects.create(name="Uber")

        self.conversation = Conversation.objects.create(
            user=self.user,
            context_type=ConversationContext.GENERAL,
        )

        for occurred_at, amount, category, merchant, description in [
            (self.fixed_now, Decimal("5.00"), TransactionCategory.GROCERIES, self.grocer, "FRESH MARKET TODAY"),
            (self.fixed_now - timedelta(days=1), Decimal("9.00"), TransactionCategory.TRANSPORT, self.ride, "UBER YESTERDAY"),
            (
                timezone.make_aware(datetime(2026, 3, 12, 10, 0, 0)),
                Decimal("10.00"),
                TransactionCategory.GROCERIES,
                self.grocer,
                "FRESH MARKET 1",
            ),
            (
                timezone.make_aware(datetime(2026, 3, 12, 18, 0, 0)),
                Decimal("14.80"),
                TransactionCategory.GROCERIES,
                self.grocer,
                "FRESH MARKET 2",
            ),
            (
                timezone.make_aware(datetime(2026, 3, 13, 9, 0, 0)),
                Decimal("11.00"),
                TransactionCategory.GROCERIES,
                self.grocer,
                "FRESH MARKET 3",
            ),
            (
                timezone.make_aware(datetime(2026, 3, 5, 13, 0, 0)),
                Decimal("40.00"),
                TransactionCategory.EATING_OUT,
                self.coffee,
                "COFFEE BAR RANGE",
            ),
            (
                timezone.make_aware(datetime(2026, 3, 7, 14, 0, 0)),
                Decimal("20.00"),
                TransactionCategory.GROCERIES,
                self.grocer,
                "FRESH MARKET RANGE",
            ),
            (
                timezone.make_aware(datetime(2025, 12, 31, 17, 0, 0)),
                Decimal("31.00"),
                TransactionCategory.GROCERIES,
                self.grocer,
                "FRESH MARKET NEW YEARS",
            ),
        ]:
            Transaction.objects.create(
                user=self.user,
                merchant=merchant,
                amount=amount,
                currency="USD",
                direction=TransactionDirection.SPEND,
                occurred_at=occurred_at,
                category=category,
                description_raw=description,
            )

    def _post_message(self, content: str):
        return self.client.post(
            f"/api/ai/conversations/{self.conversation.id}/messages/",
            {"content": content},
            format="json",
        )

    def test_spend_lookup_supports_today_and_yesterday(self):
        today_response = self._post_message("How much did I spend today?")
        yesterday_response = self._post_message("How much did I spend yesterday?")

        self.assertEqual(today_response.status_code, 201)
        self.assertIn("On March 28, 2026, you spent $5.00 across 1 transaction.", today_response.data["assistant_message"]["content"])
        self.assertEqual(yesterday_response.status_code, 201)
        self.assertIn(
            "On March 27, 2026, you spent $9.00 across 1 transaction.",
            yesterday_response.data["assistant_message"]["content"],
        )

    def test_spend_lookup_supports_exact_date_category_and_omitted_year(self):
        exact_response = self._post_message("How much did I spend on groceries on March 12?")

        self.assertEqual(exact_response.status_code, 201)
        self.assertIn(
            "On March 12, 2026, you spent $24.80 on groceries across 2 transactions.",
            exact_response.data["assistant_message"]["content"],
        )

        omitted_year_response = self._post_message("What about December 31?")

        self.assertEqual(omitted_year_response.status_code, 201)
        self.assertIn(
            "On December 31, 2025, you spent $31.00 on groceries across 1 transaction.",
            omitted_year_response.data["assistant_message"]["content"],
        )

    def test_spend_lookup_supports_explicit_range_with_category_and_zero_results(self):
        range_response = self._post_message("How much did I spend on groceries from March 1 to March 7?")
        zero_response = self._post_message("How much did I spend on groceries on March 14?")

        self.assertEqual(range_response.status_code, 201)
        self.assertIn(
            "From March 1 to March 7, 2026, you spent $20.00 on groceries across 1 transaction.",
            range_response.data["assistant_message"]["content"],
        )
        self.assertEqual(zero_response.status_code, 201)
        self.assertIn(
            "I don't see any grocery spending on March 14, 2026.",
            zero_response.data["assistant_message"]["content"],
        )

    def test_spend_lookup_supports_one_turn_refinements(self):
        initial_response = self._post_message("How much did I spend on March 12?")
        category_refinement = self._post_message("What about groceries?")
        day_refinement = self._post_message("What about March 13?")
        week_refinement = self._post_message("What about that week?")

        self.assertEqual(initial_response.status_code, 201)
        self.assertIn(
            "On March 12, 2026, you spent $24.80 across 2 transactions.",
            initial_response.data["assistant_message"]["content"],
        )
        self.assertIn(
            "On March 12, 2026, you spent $24.80 on groceries across 2 transactions.",
            category_refinement.data["assistant_message"]["content"],
        )
        self.assertIn(
            "On March 13, 2026, you spent $11.00 on groceries across 1 transaction.",
            day_refinement.data["assistant_message"]["content"],
        )
        self.assertIn(
            "From March 9 to March 15, 2026, you spent $35.80 on groceries across 3 transactions.",
            week_refinement.data["assistant_message"]["content"],
        )

        self.conversation.refresh_from_db()
        self.assertEqual(
            self.conversation.session_state_json["last_spend_lookup"],
            {
                "mode": "range",
                "date_from": "2026-03-09",
                "date_to": "2026-03-15",
                "category": "groceries",
                "window_label": None,
            },
        )


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

    def test_build_purchase_advisor_report_ignores_substring_false_positives(self):
        report = build_purchase_advisor_report(self.user, "What happened to my budget this month?")

        self.assertEqual(report["status"], "ready")
        self.assertIsNone(report["requested_category"])
        self.assertIsNone(report["targeted_report"])


class PurchaseAdvisorCategoryExtractionTests(TestCase):
    def test_extract_requested_category_matches_whole_aliases_only(self):
        self.assertEqual(extract_requested_category("Can you check my app subscriptions?"), "software")
        self.assertEqual(extract_requested_category("My rent bill is too high"), "utilities")
        self.assertEqual(extract_requested_category("I signed up for a new class"), "education")

    def test_extract_requested_category_avoids_common_substring_matches(self):
        self.assertIsNone(extract_requested_category("What happened to my budget this month?"))
        self.assertIsNone(extract_requested_category("Can you show my current spending?"))
        self.assertIsNone(extract_requested_category("Let's classify this transaction later."))


class PurchaseAdvisorChatMetadataTests(TestCase):
    def setUp(self):
        cache.clear()
        self.client = APIClient()
        self.user = User.objects.create_user(username="seed_user_2", password="testpass")
        self.client.force_authenticate(user=self.user)
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

    def tearDown(self):
        cache.clear()

    def test_messages_post_includes_purchase_advisor_report_when_enabled(self):
        response = self.client.post(
            f"/api/ai/conversations/{self.conversation.id}/messages/",
            {"content": "Am I overspending on grocery right now?"},
            format="json",
        )

        self.assertEqual(response.status_code, 201)
        report = response.data["assistant_message"]["metadata_json"]["purchase_advisor_report"]
        self.assertEqual(report["status"], "ready")
        self.assertEqual(report["requested_category"], "grocery")
        self.assertTrue(report["targeted_report"]["is_overspending"])


class AIAuthenticationTests(TestCase):
    def setUp(self):
        cache.clear()
        self.client = APIClient()
        self.user = User.objects.create_user(username="auth_user", password="testpass")
        self.conversation = Conversation.objects.create(
            user=self.user,
            context_type=ConversationContext.GENERAL,
        )

    def tearDown(self):
        cache.clear()

    def tearDown(self):
        cache.clear()

    def test_ai_messages_endpoint_accepts_authenticated_user_without_dev_header(self):
        self.client.force_authenticate(user=self.user)

        response = self.client.post(
            f"/api/ai/conversations/{self.conversation.id}/messages/",
            {"content": "Help me with my budget."},
            format="json",
        )

        self.assertEqual(response.status_code, 201)

    @override_settings(DEBUG=True)
    def test_ai_messages_endpoint_accepts_dev_header_in_debug(self):
        response = self.client.post(
            f"/api/ai/conversations/{self.conversation.id}/messages/",
            {"content": "Help me with my budget."},
            format="json",
            HTTP_X_DEV_USER="auth_user",
        )

        self.assertEqual(response.status_code, 201)

    def test_ai_messages_endpoint_rejects_dev_header_when_debug_is_disabled(self):
        response = self.client.post(
            f"/api/ai/conversations/{self.conversation.id}/messages/",
            {"content": "Help me with my budget."},
            format="json",
            HTTP_X_DEV_USER="auth_user",
        )

        self.assertEqual(response.status_code, 403)

    def test_root_messages_endpoint_is_read_only(self):
        self.client.force_authenticate(user=self.user)

        response = self.client.post(
            "/api/ai/messages/",
            {
                "conversation": self.conversation.id,
                "role": MessageRole.ASSISTANT,
                "content": "Injected assistant response",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 405)
