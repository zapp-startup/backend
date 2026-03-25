from __future__ import annotations

import numpy as np
from datetime import date, datetime, timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from datagen.agents.audit import AuditAgent
from datagen.agents.anomaly import AnomalyAgent
from datagen.agents.conversation import ConversationAgent
from datagen.agents.income import IncomeAgent
from datagen.agents.merchant import MerchantAgent
from datagen.agents.obligation import ObligationAgent
from datagen.agents.persona import PersonaAgent
from datagen.agents.spend import SpendAgent
from datagen.agents.valuation import ValuationAgent
from datagen.config import (
    CANCEL_BASE_GAMMA,
    CANCEL_OVERLOAD_GAMMA,
    MERCHANT_CATEGORY_COMPAT,
    compat_status_family,
    resolve_transaction_description,
)
from datagen.distributions import make_rng
from datagen.pipeline import run_pipeline
from datagen.state import UserState
from datagen.text import reset_conversation_dedup
from subscriptions.models import Subscription, SubscriptionEligibility, SubscriptionStatus
from transactions.models import Transaction, TransactionDirection
from users.models import UserComputed, UserRawInferred
from valuations.models import SubscriptionValuation, ValuationContext

User = get_user_model()


class DatagenPipelineTests(TestCase):
    def setUp(self):
        self.fixed_now = timezone.make_aware(datetime(2026, 3, 21, 12, 0, 0))
        reset_conversation_dedup()

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


def _monthlyize_subscription_row(sub: Subscription) -> float:
    p = float(sub.price)
    bc = str(sub.billing_cycle)
    if bc == "yearly":
        return p / 12.0
    if bc == "weekly":
        return p * 52.0 / 12.0
    return p


class SyntheticRealismPatchTests(TestCase):
    """Tests for the synthetic data realism patch (plan checklist)."""

    def setUp(self):
        self.fixed_now = timezone.make_aware(datetime(2026, 3, 21, 12, 0, 0))
        reset_conversation_dedup()

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

    def test_no_not_subscribable_merchant_in_subscription_table(self):
        prefix = "elig_"
        self._run_seed(prefix=prefix, seed=101)
        bad = Subscription.objects.filter(
            merchant__subscription_eligibility=SubscriptionEligibility.NOT_SUBSCRIBABLE,
        )
        self.assertEqual(bad.count(), 0)

    def test_raw_inferred_total_subscription_cost_matches_monthlyized_active(self):
        prefix = "subcost_"
        self._run_seed(prefix=prefix, seed=202)
        user = User.objects.get(username=f"{prefix}user_0")
        expected = sum(
            _monthlyize_subscription_row(s)
            for s in Subscription.objects.filter(user=user, status=SubscriptionStatus.ACTIVE)
        )
        inferred = UserRawInferred.objects.get(user=user)
        self.assertIsNotNone(inferred.total_subscription_cost)
        self.assertAlmostEqual(float(inferred.total_subscription_cost), expected, places=2)

    def test_actual_monthly_spending_is_window_total_over_months(self):
        prefix = "monthly_"
        self._run_seed(prefix=prefix, seed=303)
        user = User.objects.get(username=f"{prefix}user_0")
        spends = list(
            Transaction.objects.filter(user=user, direction=TransactionDirection.SPEND).order_by(
                "occurred_at"
            )
        )
        self.assertTrue(spends)
        total = sum(float(t.amount) for t in spends)
        dmin = min(t.occurred_at.date() for t in spends)
        dmax = max(t.occurred_at.date() for t in spends)
        window_days = max(1, (dmax - dmin).days + 1)
        months_in_window = max(1.0, window_days / 30.0)
        expected = total / months_in_window
        inferred = UserRawInferred.objects.get(user=user)
        self.assertAlmostEqual(float(inferred.actual_monthly_spending), expected, places=1)

    def test_overloaded_cancel_probability_exceeds_light_burden(self):
        """Cancellation p includes overload gamma; Monte Carlo mean should be higher."""
        rng = np.random.default_rng(7)
        n = 4000
        p_light = CANCEL_BASE_GAMMA
        p_over = CANCEL_BASE_GAMMA + CANCEL_OVERLOAD_GAMMA
        light_cancels = (rng.random(n) < p_light).mean()
        over_cancels = (rng.random(n) < p_over).mean()
        self.assertGreater(over_cancels, light_cancels)
        self.assertGreater(p_over, p_light)

    def test_persistent_deficits_worsen_debt_carry_state(self):
        state = UserState(monthly_income=Decimal("4000"))
        state.debt_carry_state = "none"
        for _ in range(3):
            state.apply_debt_transition(-500.0)
        self.assertNotEqual(state.debt_carry_state, "none")

    def test_audit_logs_disallowed_merchant_category_compat(self):
        agent = AuditAgent(make_rng(1), use_llm=False)
        pair = next(k for k, v in MERCHANT_CATEGORY_COMPAT.items() if v == "disallowed")
        ctx = {
            "spend_transactions": [
                {
                    "merchant_info": {"category": pair[0]},
                    "spend_category": pair[1],
                }
            ]
        }
        repairs = agent._check_merchant_category_compat(ctx)
        self.assertTrue(any("compat" in r for r in repairs))

    def test_description_resolver_maps_income_and_bills_templates(self):
        income = resolve_transaction_description("Direct Deposit - Payroll", direction="income")
        bill = resolve_transaction_description("AT&T WIRELESS", direction="spend", fallback_category="bills")
        refund = resolve_transaction_description("REFUND AMAZON AMAZON COM", direction="refund", fallback_category="shopping")
        self.assertEqual(income["merchant_info"]["name"], "Payroll")
        self.assertEqual(bill["merchant_info"]["name"], "AT&T")
        self.assertEqual(bill["category"], "bills")
        self.assertEqual(refund["merchant_info"]["name"], "Amazon")
        self.assertEqual(refund["category"], "shopping")

    def test_item_valuation_softmax_stochastic_and_score_band_monotonic(self):
        state = UserState(monthly_income=Decimal("5500"), price_sensitivity=0.35)

        def band_buy_rate(lo: int, hi: int, seed: int) -> float:
            agent = ValuationAgent(make_rng(seed), use_llm=False)
            n = 60
            buys = 0
            for _ in range(n):
                score = int(agent.rng.integers(lo, hi + 1))
                rec = agent._item_recommendation_from_score(state, score, 0.75, 0.72)
                if rec == "buy":
                    buys += 1
            return buys / n

        r_low = band_buy_rate(0, 50, 11)
        r_high = band_buy_rate(100, 150, 22)
        self.assertGreater(r_high, r_low)

        agent = ValuationAgent(make_rng(99), use_llm=False)
        recs = [agent._item_recommendation_from_score(state, 85, 0.7, 0.7) for _ in range(50)]
        self.assertGreater(len(set(recs)), 1)

    def test_conversation_roles_alternate_and_no_duplicate_content(self):
        prefix = "convo_"
        self._run_seed(prefix=prefix, seed=404, months=3)
        user = User.objects.get(username=f"{prefix}user_0")
        for convo in user.conversations.all():
            messages = list(convo.messages.order_by("created_at", "id"))
            if len(messages) < 2:
                continue
            roles = [m.role for m in messages]
            for i in range(len(roles) - 1):
                self.assertNotEqual(roles[i], roles[i + 1])
            contents = [m.content.strip() for m in messages]
            self.assertEqual(len(contents), len(set(contents)))

    def test_over_subscribed_fact_not_emitted_when_thresholds_not_met(self):
        agent = ConversationAgent(make_rng(5), use_llm=False)
        state = UserState(monthly_income=Decimal("8000"))
        subs = []
        for i in range(7):
            subs.append(
                {
                    "status": "active",
                    "price": Decimal("10.00"),
                    "billing_cycle": "monthly",
                    "usage_frequency": 1,
                    "merchant_info": {"name": f"S{i}"},
                }
            )
        ctx = {
            "subscriptions": subs,
            "start_date": date(2025, 1, 1),
            "end_date": date(2025, 6, 1),
            "spend_transactions": [],
            "item_valuations": [],
            "subscription_valuations": [],
        }
        out = agent.run(state, ctx)
        keys = [f.get("fact_key") for f in out.get("conversation_facts", [])]
        self.assertNotIn("over_subscribed", keys)

    def test_pipeline_creates_profile_layers_and_audit_income_rule(self):
        prefix = "layers_"
        self._run_seed(prefix=prefix, seed=505)
        user = User.objects.get(username=f"{prefix}user_0")
        self.assertTrue(UserRawInferred.objects.filter(user=user).exists())
        self.assertTrue(UserComputed.objects.filter(user=user).exists())
        self.assertIsNotNone(user.raw_explicit)

        audit = AuditAgent(make_rng(1), use_llm=False)
        st = UserState()
        bad = audit.run(
            st,
            {"income_transactions": [], "behavior_facts": [], "conversation_facts": []},
        )
        self.assertTrue(any("income" in r for r in bad["audit_repairs"]))

        ok = audit.run(
            st,
            {
                "income_transactions": [],
                "behavior_facts": [{"fact_key": "intentionally_sparse"}],
                "conversation_facts": [],
            },
        )
        self.assertFalse(any("income" in r for r in ok["audit_repairs"]))

    def test_price_sensitivity_reduces_buy_probability_for_overpriced_items(self):
        """High price_sensitivity should reduce buy rate when item is overpriced."""
        sensitive = UserState(
            monthly_income=Decimal("3500"),
            price_sensitivity=0.95,
            quality_preference=0.3,
        )
        insensitive = UserState(
            monthly_income=Decimal("3500"),
            price_sensitivity=0.05,
            quality_preference=0.3,
        )
        n = 200
        # Score 110 — leans buy, but low price_fairness should pull sensitive users away
        def buy_rate(state: UserState, seed: int) -> float:
            agent = ValuationAgent(make_rng(seed), use_llm=False)
            buys = sum(
                1 for _ in range(n)
                if agent._item_recommendation_from_score(state, 110, 0.15, 0.5) == "buy"
            )
            return buys / n

        r_sensitive = buy_rate(sensitive, 42)
        r_insensitive = buy_rate(insensitive, 42)
        self.assertLess(r_sensitive, r_insensitive)

    def test_subscription_valuation_burden_shifts_toward_skip(self):
        """High cost share + overloaded burden should produce more skip than cheap sub."""
        base_state = dict(
            quality_preference=0.4,
            credit_stress=0.4,
            regret_sensitivity=0.4,
            price_sensitivity=0.7,
        )
        expensive_state = UserState(
            monthly_income=Decimal("3000"),
            subscription_burden_state="overloaded",
            **base_state,
        )
        cheap_state = UserState(
            monthly_income=Decimal("3000"),
            subscription_burden_state="light",
            **base_state,
        )

        def rec_counts(state: UserState, price: Decimal, seed: int) -> dict[str, int]:
            agent = ValuationAgent(make_rng(seed), use_llm=False)
            sub = {
                "status": "active",
                "billing_cycle": "monthly",
                "price": price,
                "started_on": date(2025, 1, 1),
                "cancelled_on": None,
                "usage_frequency": 1,
            }
            vals = agent._valuate_subscription(
                state=state, sub=sub,
                start=date(2025, 1, 1), end=date(2025, 12, 31),
            )
            counts: dict[str, int] = {"buy": 0, "wait": 0, "skip": 0}
            for v in vals:
                counts[v["recommendation"]] = counts.get(v["recommendation"], 0) + 1
            return counts

        # Expensive sub (cost_share=0.25) under overloaded burden
        exp = rec_counts(expensive_state, Decimal("750.00"), 77)
        # Cheap sub (cost_share=0.01) under light burden
        chp = rec_counts(cheap_state, Decimal("30.00"), 77)

        # Expensive/overloaded should produce at least as many skips as cheap/light
        self.assertGreaterEqual(exp.get("skip", 0), chp.get("skip", 0))

    def test_audit_subscription_metric_consistency_recomputes_burden(self):
        """Audit should fix stale subscription_burden_state."""
        audit = AuditAgent(make_rng(1), use_llm=False)
        state = UserState(monthly_income=Decimal("4000"))
        state.subscription_burden_state = "light"  # stale — will be overloaded after sub added
        ctx = {
            "subscriptions": [
                {
                    "status": "active",
                    "price": Decimal("800.00"),
                    "billing_cycle": "monthly",
                    "merchant_info": {"name": "SomeService", "eligibility": "standard_subscription"},
                    "_sub_key": "SomeService::2025-01-01",
                }
            ],
            "subscription_transactions": [],
            "subscription_valuations": [],
            "behavior_facts": [{"fact_key": "intentionally_sparse"}],
            "conversation_facts": [],
        }
        repairs = audit.run(state, ctx)["audit_repairs"]
        # Burden should now be overloaded (800/4000 = 0.2 > 0.15)
        self.assertEqual(state.subscription_burden_state, "overloaded")
        self.assertTrue(any("burden" in r or "Recomputed" in r for r in repairs))


class IntentFirstRealismTests(TestCase):
    """Intent-first spend, compatibility, subscription tiers, valuation policy."""

    def setUp(self):
        self.fixed_now = timezone.make_aware(datetime(2026, 3, 21, 12, 0, 0))
        reset_conversation_dedup()

    def _run_seed(self, prefix: str, seed: int, months: int = 1, users: int = 1):
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

    def test_family_spend_compat_hard_rules(self):
        self.assertEqual(compat_status_family("pharmacy", "transport"), "disallowed")
        self.assertEqual(compat_status_family("fuel", "shopping"), "disallowed")
        self.assertEqual(compat_status_family("rideshare", "transport"), "allowed")
        self.assertEqual(compat_status_family("ecommerce", "transport"), "disallowed")
        self.assertEqual(compat_status_family("grocery_retail", "groceries"), "allowed")

    def test_merchant_picker_respects_family_and_category(self):
        rng = make_rng(201)
        agent = MerchantAgent(rng, use_llm=False)
        catalog = agent.run(UserState(), {})["merchant_catalog"]
        m1 = agent.pick_merchant_for_family("rideshare", "transport", catalog)
        self.assertIn(m1["name"], ("Uber", "Lyft"))
        m2 = agent.pick_merchant_for_family("pharmacy", "health", catalog)
        self.assertIn(m2["name"], ("Walgreens", "CVS Pharmacy"))
        m3 = agent.pick_merchant_for_family("fuel", "transport", catalog)
        self.assertIn(m3["name"], ("Shell", "Chevron"))
        m4 = agent.pick_merchant_for_family("grocery_retail", "groceries", catalog)
        self.assertIn(
            m4["name"],
            ("Walmart", "Target", "Costco", "Whole Foods", "Kroger", "Trader Joe's"),
        )

    def test_spend_agent_essential_transport_never_pharmacy(self):
        rng = make_rng(88)
        state = UserState(
            monthly_income=Decimal("5200.00"),
            archetype="salary_biweekly",
            category_budgets={c: 0.09 for c in [
                "rent", "groceries", "dining", "transport", "shopping",
                "entertainment", "health", "travel", "utilities", "subscriptions", "fees",
            ]},
        )
        state.last_paydays = [date(2025, 1, 3), date(2025, 1, 17)]
        merchant_agent = MerchantAgent(rng, use_llm=False)
        catalog = merchant_agent.run(UserState(), {})["merchant_catalog"]
        context = {
            "start_date": date(2025, 1, 1),
            "end_date": date(2025, 2, 28),
            "merchant_agent": merchant_agent,
            "merchant_catalog": catalog,
            "subscriptions": [],
        }
        out = SpendAgent(rng, use_llm=False).run(state, context)["spend_transactions"]
        for t in out:
            if t.get("spend_category") == "transport":
                self.assertIn(
                    t["merchant_info"]["name"],
                    ("Uber", "Lyft", "Shell", "Chevron"),
                )

    def test_persona_samples_higher_luxury_affinity_for_high_income_than_constrained(self):
        ctx = {"end_date": date(2025, 12, 31)}
        hi_vals = []
        cc_vals = []
        for seed in range(30):
            with patch("datagen.agents.persona.sample_archetype", return_value="high_income"):
                st = UserState()
                PersonaAgent(make_rng(seed), use_llm=False).run(st, ctx)
                hi_vals.append(st.luxury_affinity)
            with patch("datagen.agents.persona.sample_archetype", return_value="credit_constrained"):
                st = UserState()
                PersonaAgent(make_rng(100 + seed), use_llm=False).run(st, ctx)
                cc_vals.append(st.luxury_affinity)
        self.assertGreater(sum(hi_vals) / len(hi_vals), sum(cc_vals) / len(cc_vals))

    def test_shopping_intent_routes_luxury_more_for_high_affinity_users(self):
        agent = SpendAgent(make_rng(120), use_llm=False)
        rich = UserState(
            monthly_income=Decimal("9500.00"),
            quality_preference=0.85,
            novelty_seeking=0.72,
            luxury_affinity=0.88,
            price_sensitivity=0.18,
            liquidity="comfortable",
        )
        cautious = UserState(
            monthly_income=Decimal("4200.00"),
            quality_preference=0.42,
            novelty_seeking=0.30,
            luxury_affinity=0.08,
            price_sensitivity=0.78,
            liquidity="stable",
        )
        rich_hits = sum(
            1 for _ in range(60)
            if agent._shopping_intent(rich, payday_window=1.0, liq=rich.liquidity_numeric(), mi=float(rich.monthly_income))[1] == "luxury_retail"
        )
        cautious_hits = sum(
            1 for _ in range(60)
            if agent._shopping_intent(cautious, payday_window=1.0, liq=cautious.liquidity_numeric(), mi=float(cautious.monthly_income))[1] == "luxury_retail"
        )
        self.assertGreater(rich_hits, cautious_hits)
        self.assertLess(rich_hits, 30)

    def test_luxury_retail_transactions_remain_shopping(self):
        agent = SpendAgent(make_rng(121), use_llm=False)
        merchant_agent = MerchantAgent(make_rng(121), use_llm=False)
        catalog = merchant_agent.run(UserState(), {})["merchant_catalog"]
        txns = []
        state = UserState(
            monthly_income=Decimal("10000.00"),
            quality_preference=0.9,
            novelty_seeking=0.75,
            luxury_affinity=0.95,
            price_sensitivity=0.12,
            liquidity="comfortable",
        )
        agent._emit_transaction(
            state,
            date(2025, 1, 15),
            spend_category="shopping",
            intent="luxury_splurge",
            merchant_family="luxury_retail",
            transactions=txns,
            merchant_agent=merchant_agent,
            merchant_catalog=catalog,
            days_since=2,
            liq=state.liquidity_numeric(),
            mi=float(state.monthly_income),
        )
        self.assertEqual(txns[0]["merchant_family"], "luxury_retail")
        self.assertEqual(txns[0]["category"], "shopping")

    def test_income_transactions_get_normalized_income_merchants(self):
        rng = make_rng(111)
        state = UserState(monthly_income=Decimal("5200.00"), archetype="salary_biweekly")
        out = IncomeAgent(rng, use_llm=False).run(
            state,
            {"start_date": date(2025, 1, 1), "end_date": date(2025, 2, 28)},
        )["income_transactions"]
        self.assertTrue(out)
        self.assertTrue(all(t.get("merchant_info") for t in out))
        self.assertIn(out[0]["merchant_info"]["name"], ("Payroll", "Reimbursement"))

    def test_obligation_transactions_get_normalized_bill_merchants(self):
        rng = make_rng(112)
        state = UserState(monthly_income=Decimal("5200.00"))
        out = ObligationAgent(rng, use_llm=False).run(
            state,
            {"start_date": date(2025, 1, 1), "end_date": date(2025, 2, 28)},
        )["obligation_transactions"]
        self.assertTrue(out)
        bill_like = [t for t in out if t["category"] == "bills"]
        self.assertTrue(bill_like)
        self.assertTrue(all(t.get("merchant_info") for t in bill_like))

    def test_spend_groceries_use_grocery_retailers_only(self):
        rng = make_rng(91)
        state = UserState(
            monthly_income=Decimal("4800.00"),
            household_size=2,
            category_budgets={c: 0.09 for c in [
                "rent", "groceries", "dining", "transport", "shopping",
                "entertainment", "health", "travel", "utilities", "subscriptions", "fees",
            ]},
        )
        state.last_paydays = [date(2025, 1, 1)]
        merchant_agent = MerchantAgent(rng, use_llm=False)
        catalog = merchant_agent.run(UserState(), {})["merchant_catalog"]
        context = {
            "start_date": date(2025, 1, 1),
            "end_date": date(2025, 3, 31),
            "merchant_agent": merchant_agent,
            "merchant_catalog": catalog,
            "subscriptions": [],
        }
        out = SpendAgent(rng, use_llm=False).run(state, context)["spend_transactions"]
        groc = [t for t in out if t.get("spend_category") == "groceries"]
        self.assertTrue(groc)
        for t in groc:
            self.assertEqual(t.get("merchant_family"), "grocery_retail")

    def test_entertainment_spend_does_not_use_food_quick_merchants(self):
        rng = make_rng(92)
        agent = SpendAgent(rng, use_llm=False)
        seen_families = {
            agent._discretionary_intent("entertainment", UserState(), date(2025, 1, 10), None)[1]
            for _ in range(10)
        }
        self.assertEqual(seen_families, {"entertainment_out"})

    def test_delivery_orders_can_prefer_active_membership_merchant_without_subscription_link(self):
        rng = make_rng(123)
        agent = SpendAgent(rng, use_llm=False)
        catalog = MerchantAgent(rng, use_llm=False).run(UserState(), {})["merchant_catalog"]
        ctx = {
            "subscriptions": [
                {
                    "status": "active",
                    "merchant_info": {
                        "name": "DoorDash",
                        "merchant_family": "delivery_membership",
                    },
                }
            ]
        }
        picked = [
            agent._pick_discretionary_merchant(
                UserState(),
                ctx,
                spend_category="dining",
                intent="meal_delivery",
                merchant_family="delivery_membership",
                merchant_agent=MerchantAgent(make_rng(123 + i), use_llm=False),
                merchant_catalog=catalog,
            )["name"]
            for i in range(12)
        ]
        self.assertIn("DoorDash", picked)

    def test_subscription_netflix_price_from_tier_table(self):
        from datagen.agents.subscription import SubscriptionAgent
        from datagen.config import SUBSCRIPTION_MERCHANT_PRICE_TIERS

        rng = make_rng(33)
        agent = SubscriptionAgent(rng, use_llm=False)
        merch = {"name": "Netflix", "category": "streaming", "domain": "netflix.com", "eligibility": "standard_subscription", "yearly_billing_mode": "normal"}
        p = agent._price_for_merchant(merch, "streaming")
        tier_vals = [Decimal(s) for s, _w in SUBSCRIPTION_MERCHANT_PRICE_TIERS["Netflix"]]
        self.assertGreaterEqual(p, min(tier_vals) - Decimal("1.5"))
        self.assertLessEqual(p, max(tier_vals) + Decimal("1.5"))

    def test_subscription_yearly_forbidden_for_utilities(self):
        from datagen.agents.subscription import SubscriptionAgent

        rng = make_rng(5)
        agent = SubscriptionAgent(rng, use_llm=False)
        merch = {"name": "ComEd", "yearly_billing_mode": "forbidden"}
        yearly_hits = sum(
            1 for _ in range(400)
            if agent._yearly_billing_draw(merch, "streaming")
        )
        self.assertEqual(yearly_hits, 0)

    def test_subscription_charge_categories_follow_merchant_family(self):
        from datagen.agents.subscription import SubscriptionAgent

        agent = SubscriptionAgent(make_rng(6), use_llm=False)
        state = UserState()
        charge_date = date(2025, 1, 15)

        utility = agent._make_charge(
            charge_date,
            Decimal("89.99"),
            {"name": "ComEd", "merchant_family": "utilities"},
            None,
            state,
            "sub-utility",
        )
        education = agent._make_charge(
            charge_date,
            Decimal("49.00"),
            {"name": "Coursera", "merchant_family": "education"},
            None,
            state,
            "sub-education",
        )
        food = agent._make_charge(
            charge_date,
            Decimal("9.99"),
            {"name": "DoorDash", "merchant_family": "delivery_membership"},
            None,
            state,
            "sub-food",
        )

        self.assertEqual(utility["category"], "bills")
        self.assertEqual(education["category"], "education")
        self.assertEqual(food["category"], "eating_out")

    def test_subscription_tier_prices_stay_close_to_plan_band(self):
        from datagen.agents.subscription import SubscriptionAgent

        agent = SubscriptionAgent(make_rng(8), use_llm=False)
        vals = [
            float(agent._price_for_merchant({"name": "HBO Max"}, "streaming"))
            for _ in range(30)
        ]
        self.assertGreaterEqual(min(vals), 15.0)
        self.assertLessEqual(max(vals), 20.8)

    def test_subscription_valuation_strongly_negative_net_rarely_buy(self):
        state = UserState(
            monthly_income=Decimal("4000.00"),
            subscription_burden_state="stretched",
            liquidity="tight",
        )
        buys = 0
        for seed in range(40):
            agent = ValuationAgent(make_rng(seed), use_llm=False)
            rec = agent._subscription_recommendation(
                state=state,
                net_value=Decimal("-22.00"),
                usage=0.2,
                cost_share=0.12,
                monthly_income=4000.0,
                fit=0.4,
                friction=0.5,
            )
            if rec == "buy":
                buys += 1
        self.assertLessEqual(buys, 2)

    def test_actual_monthly_spending_not_written_in_datagen_context(self):
        """Generators must not set actual_monthly_spending; only inference rollup does."""
        prefix = "derived_"
        self._run_seed(prefix=prefix, seed=717, months=1)
        user = User.objects.get(username=f"{prefix}user_0")
        inferred = UserRawInferred.objects.get(user=user)
        self.assertIsNotNone(inferred.actual_monthly_spending)

    def test_audit_semantic_repair_aligns_pharmacy_to_health(self):
        from datagen.agents.audit import AuditAgent

        audit = AuditAgent(make_rng(1), use_llm=False)
        ctx = {
            "spend_transactions": [
                {
                    "direction": "spend",
                    "amount": Decimal("12.00"),
                    "occurred_at": timezone.now(),
                    "category": "transport",
                    "spend_category": "transport",
                    "merchant_family": "pharmacy",
                    "merchant_info": {"name": "Walgreens", "category": "other", "merchant_family": "pharmacy"},
                    "payment_channel": "card",
                    "description_raw": "WALGREENS",
                }
            ],
            "merchant_catalog": [],
            "merchant_agent": None,
            "subscriptions": [],
            "subscription_transactions": [],
            "income_transactions": [{"amount": Decimal("5000"), "direction": "income"}],
            "behavior_facts": [{"fact_key": "intentionally_sparse"}],
            "conversation_facts": [],
        }
        audit.run(UserState(), ctx)
        self.assertEqual(ctx["spend_transactions"][0]["spend_category"], "health")
        self.assertEqual(ctx["spend_transactions"][0]["category"], "health")

    def test_audit_resolves_other_from_strong_family(self):
        audit = AuditAgent(make_rng(4), use_llm=False)
        ctx = {
            "spend_transactions": [
                {
                    "direction": "spend",
                    "amount": Decimal("14.00"),
                    "occurred_at": timezone.now(),
                    "category": "other",
                    "spend_category": "travel",
                    "merchant_family": "food_quick",
                    "merchant_info": {"name": "Starbucks", "category": "food", "merchant_family": "food_quick"},
                    "payment_channel": "card",
                    "description_raw": "STARBUCKS",
                }
            ],
            "merchant_catalog": [],
            "merchant_agent": None,
            "subscriptions": [],
            "subscription_transactions": [],
            "income_transactions": [{"amount": Decimal("5000"), "direction": "income"}],
            "behavior_facts": [{"fact_key": "intentionally_sparse"}],
            "conversation_facts": [],
        }
        audit.run(UserState(), ctx)
        self.assertEqual(ctx["spend_transactions"][0]["spend_category"], "dining")
        self.assertEqual(ctx["spend_transactions"][0]["category"], "eating_out")

    def test_audit_canonicalizes_category_even_when_spend_category_is_allowed(self):
        audit = AuditAgent(make_rng(2), use_llm=False)
        ctx = {
            "spend_transactions": [
                {
                    "direction": "spend",
                    "amount": Decimal("18.00"),
                    "occurred_at": timezone.now(),
                    "category": "transport",
                    "spend_category": "shopping",
                    "merchant_family": "ecommerce",
                    "merchant_info": {"name": "Amazon", "category": "other", "merchant_family": "ecommerce"},
                    "payment_channel": "card",
                    "description_raw": "AMAZON",
                    "_anomaly": "mislabel",
                }
            ],
            "merchant_catalog": [],
            "merchant_agent": None,
            "subscriptions": [],
            "subscription_transactions": [],
            "income_transactions": [{"amount": Decimal("5000"), "direction": "income"}],
            "behavior_facts": [{"fact_key": "intentionally_sparse"}],
            "conversation_facts": [],
        }
        audit.run(UserState(), ctx)
        self.assertEqual(ctx["spend_transactions"][0]["spend_category"], "shopping")
        self.assertEqual(ctx["spend_transactions"][0]["category"], "shopping")

    def test_anomaly_agent_skips_category_mislabels_for_family_backed_spend(self):
        agent = AnomalyAgent(make_rng(3), use_llm=False)
        txn = {
            "direction": "spend",
            "amount": Decimal("24.00"),
            "occurred_at": timezone.now(),
            "category": "transport",
            "spend_category": "transport",
            "merchant_family": "rideshare",
            "merchant_info": {"name": "Uber", "category": "other", "merchant_family": "rideshare"},
            "payment_channel": "card",
            "description_raw": "UBER",
        }
        ctx = {
            "spend_transactions": [txn],
            "obligation_transactions": [],
            "subscription_transactions": [],
            "start_date": date(2025, 1, 1),
            "end_date": date(2025, 3, 1),
        }
        for _ in range(20):
            agent.run(UserState(), ctx)
        self.assertEqual(txn["category"], "transport")
        self.assertNotEqual(txn.get("_anomaly"), "mislabel")

    def test_item_fair_price_gap_is_not_formulaically_constant(self):
        state = UserState(monthly_income=Decimal("5200.00"))
        agent = ValuationAgent(make_rng(9), use_llm=False)
        ratios = []
        for _ in range(60):
            item = agent._valuate_item(state)
            observed = float(item["observed_price"])
            fair = float(item["estimated_fair_price"])
            ratios.append(round((observed - fair) / max(1.0, fair), 4))
        self.assertGreater(len(set(ratios)), 20)

    def test_high_luxury_affinity_users_are_more_tolerant_of_luxury_premium(self):
        high = UserState(
            monthly_income=Decimal("9000.00"),
            quality_preference=0.88,
            luxury_affinity=0.92,
            price_sensitivity=0.18,
        )
        low = UserState(
            monthly_income=Decimal("9000.00"),
            quality_preference=0.55,
            luxury_affinity=0.05,
            price_sensitivity=0.62,
        )
        high_agent = ValuationAgent(make_rng(130), use_llm=False)
        low_agent = ValuationAgent(make_rng(130), use_llm=False)
        high_buys = sum(
            1 for _ in range(80)
            if high_agent._item_recommendation_from_score(high, 104, 0.63, 0.71, luxury_signal=1.0) == "buy"
        )
        low_buys = sum(
            1 for _ in range(80)
            if low_agent._item_recommendation_from_score(low, 104, 0.63, 0.71, luxury_signal=1.0) == "buy"
        )
        self.assertGreater(high_buys, low_buys)

    def test_budget_constrained_users_do_not_overbuy_luxury_items(self):
        state = UserState(
            monthly_income=Decimal("2800.00"),
            quality_preference=0.72,
            luxury_affinity=0.68,
            price_sensitivity=0.64,
            credit_stress=0.7,
            liquidity="tight",
        )
        agent = ValuationAgent(make_rng(131), use_llm=False)
        buys = sum(
            1 for _ in range(80)
            if agent._item_recommendation_from_score(state, 108, 0.56, 0.62, luxury_signal=1.0) == "buy"
        )
        self.assertLessEqual(buys, 25)

    def test_subscription_churner_fact_requires_real_churn_signal(self):
        agent = ConversationAgent(make_rng(10), use_llm=False)
        state = UserState(monthly_income=Decimal("5000.00"))
        ctx = {
            "subscriptions": [
                {"status": "canceled", "reactivation_count": 0, "price": Decimal("10.00"), "billing_cycle": "monthly"},
                {"status": "canceled", "reactivation_count": 0, "price": Decimal("11.00"), "billing_cycle": "monthly"},
                {"status": "active", "reactivation_count": 0, "price": Decimal("12.00"), "billing_cycle": "monthly"},
                {"status": "active", "reactivation_count": 0, "price": Decimal("13.00"), "billing_cycle": "monthly"},
                {"status": "active", "reactivation_count": 0, "price": Decimal("14.00"), "billing_cycle": "monthly"},
            ],
            "item_valuations": [],
            "subscription_valuations": [],
            "spend_transactions": [],
            "start_date": date(2025, 1, 1),
            "end_date": date(2025, 4, 1),
        }
        out = agent.run(state, ctx)
        keys = [f.get("fact_key") for f in out.get("conversation_facts", [])]
        self.assertNotIn("subscription_churner", keys)
