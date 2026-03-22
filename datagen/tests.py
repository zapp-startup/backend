from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from datagen.agents.valuation import ValuationAgent
from datagen.distributions import make_rng
from datagen.pipeline import run_pipeline
from datagen.state import UserState
from subscriptions.models import Subscription, SubscriptionStatus
from valuations.models import SubscriptionValuation, ValuationContext

User = get_user_model()


class DatagenPipelineTests(TestCase):
    def setUp(self):
        self.fixed_now = timezone.make_aware(datetime(2026, 3, 21, 12, 0, 0))

    def _run_seed(self, prefix: str, seed: int, months: int = 2, users: int = 1):
        with patch("datagen.pipeline.timezone.now", return_value=self.fixed_now):
            return run_pipeline(
                num_users=users,
                months=months,
                prefix=prefix,
                password="password123",
                use_llm=False,
                seed=seed,
                log=lambda _msg: None,
            )

    def _snapshot_for_prefix(self, prefix: str) -> dict:
        user = User.objects.get(username=f"{prefix}user_0")
        txn = user.transactions.order_by("occurred_at", "id").first()
        convo = user.conversations.order_by("created_at", "id").first()
        first_message = convo.messages.order_by("created_at", "id").first() if convo else None
        return {
            "first_name": user.first_name,
            "last_name": user.last_name,
            "dob": user.raw_explicit.dob.isoformat(),
            "zip": user.raw_explicit.location_zip,
            "goal": user.raw_explicit.financial_goal,
            "txn_amount": str(txn.amount),
            "txn_occurred_at": txn.occurred_at.isoformat(),
            "txn_description": txn.description_raw,
            "convo_title": convo.title if convo else None,
            "convo_created_at": convo.created_at.isoformat() if convo else None,
            "message_content": first_message.content if first_message else None,
            "message_created_at": first_message.created_at.isoformat() if first_message else None,
        }

    def test_seeded_pipeline_is_reproducible(self):
        prefix = "deterministic_"
        self._run_seed(prefix=prefix, seed=17)
        first_snapshot = self._snapshot_for_prefix(prefix)

        User.objects.filter(username__startswith=prefix).delete()

        self._run_seed(prefix=prefix, seed=17)
        second_snapshot = self._snapshot_for_prefix(prefix)

        self.assertEqual(first_snapshot, second_snapshot)

    def test_pipeline_persists_historical_relationships_and_valid_choices(self):
        prefix = "integrity_"
        self._run_seed(prefix=prefix, seed=23)
        user = User.objects.get(username=f"{prefix}user_0")

        active_pairs = list(
            Subscription.objects.filter(user=user, status=SubscriptionStatus.ACTIVE)
            .values_list("merchant_id", flat=True)
        )
        self.assertEqual(len(active_pairs), len(set(active_pairs)))

        valuations = list(
            SubscriptionValuation.objects.filter(user=user).select_related("subscription")
        )
        self.assertTrue(valuations)
        valid_contexts = {choice for choice, _label in ValuationContext.choices}
        for valuation in valuations:
            self.assertEqual(valuation.subscription.user_id, user.id)
            self.assertIn(valuation.context, valid_contexts)
            self.assertGreaterEqual(valuation.period_start, valuation.subscription.started_on)
            if valuation.subscription.cancelled_on:
                self.assertLessEqual(valuation.period_end, valuation.subscription.cancelled_on)

        conversations = list(user.conversations.prefetch_related("messages"))
        self.assertTrue(conversations)
        for conversation in conversations:
            if conversation.linked_subscription_id:
                self.assertEqual(conversation.linked_subscription.user_id, user.id)
            if conversation.linked_item_valuation_id:
                self.assertEqual(conversation.linked_item_valuation.user_id, user.id)

            messages = list(conversation.messages.all())
            self.assertGreaterEqual(len(messages), 2)
            self.assertEqual(
                [message.created_at for message in messages],
                sorted(message.created_at for message in messages),
            )
            self.assertLessEqual(conversation.created_at, messages[0].created_at)
            self.assertEqual(conversation.updated_at, messages[-1].created_at)

        self.assertTrue(any(convo.created_at < self.fixed_now - timedelta(minutes=5) for convo in conversations))


class ValuationAgentTests(TestCase):
    def test_subscription_valuations_respect_lifecycle_and_yearly_costs(self):
        agent = ValuationAgent(make_rng(7), use_llm=False)
        state = UserState(
            monthly_income=Decimal("6000.00"),
            quality_preference=0.62,
            credit_stress=0.28,
            regret_sensitivity=0.35,
        )
        subscription = {
            "status": "canceled",
            "billing_cycle": "yearly",
            "price": Decimal("120.00"),
            "started_on": date(2025, 1, 15),
            "cancelled_on": date(2025, 4, 10),
            "usage_frequency": 3,
        }

        valuations = agent._valuate_subscription(
            state=state,
            sub=subscription,
            start=date(2025, 1, 1),
            end=date(2025, 12, 31),
        )

        self.assertTrue(valuations)
        self.assertEqual(valuations[-1]["context"], "subscription_cancel")
        for valuation in valuations:
            self.assertGreaterEqual(valuation["period_start"], subscription["started_on"])
            self.assertLessEqual(valuation["period_end"], subscription["cancelled_on"])
            self.assertLessEqual(valuation["total_cost"], Decimal("120.00"))
            self.assertIn(valuation["context"], {"subscription_renewal", "subscription_cancel"})
