"""
Agent 9: Valuation Agent (Section 15)
Creates synthetic ground-truth valuation outputs for items and subscriptions.
Uses utility-based formulas with structured JSON evidence.
Writes to: valuations_subscriptionvaluation, valuations_itemvaluation
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from datagen.agents.base import BaseAgent
from datagen.config import (
    ITEM_REC_THRESHOLDS,
    ITEM_VALUE_WEIGHTS,
    SUBSCRIPTION_REC_THRESHOLDS,
    SUBSCRIPTION_VALUE_WEIGHTS,
    VALUATION_SCORE_BANDS,
)
from datagen.distributions import clip01
from datagen.state import UserState
from datagen.text import generate_explanation_json

ITEM_POOL = [
    ("AirPods Pro", "electronics", 180, 250),
    ("MacBook Air", "electronics", 900, 1300),
    ("iPad", "electronics", 350, 600),
    ("Running Shoes", "fitness", 80, 180),
    ("Protein Powder", "fitness", 25, 60),
    ("Yoga Mat", "fitness", 20, 80),
    ("Textbook", "education", 30, 120),
    ("Online Course Bundle", "education", 50, 200),
    ("Coffee Maker", "home", 40, 200),
    ("Office Chair", "home", 150, 500),
    ("Monitor", "electronics", 200, 600),
    ("Winter Jacket", "other", 80, 300),
    ("Backpack", "other", 40, 120),
    ("Noise Cancelling Headphones", "electronics", 150, 350),
    ("Standing Desk", "home", 250, 700),
    ("Instant Pot", "home", 60, 150),
    ("Skincare Set", "health", 30, 100),
    ("Vitamins Bundle", "health", 20, 60),
]


class ValuationAgent(BaseAgent):
    """Generate subscription and item valuations with structured evidence."""

    def run(self, state: UserState, context: dict) -> dict:
        rng = self.rng
        subscriptions = context.get("subscriptions", [])
        spend_txns = context.get("spend_transactions", [])
        start_date: date = context["start_date"]
        end_date: date = context["end_date"]

        sub_valuations = []
        item_valuations = []

        # Subscription valuations (Section 15.4)
        for sub in subscriptions:
            vals = self._valuate_subscription(state, sub, start_date, end_date)
            sub_valuations.extend(vals)

        # Item valuations for ~90% of spend transactions (product feedback)
        for txn in spend_txns:
            if rng.random() < 0.90:
                val = self._valuate_spend_transaction(state, txn)
                if val:
                    item_valuations.append(val)

        # Additional valuations from ITEM_POOL for variety (fewer)
        n_extra = max(0, int(rng.poisson(2)))
        for _ in range(n_extra):
            item_valuations.append(self._valuate_item(state))

        return {
            "subscription_valuations": sub_valuations,
            "item_valuations": item_valuations,
        }

    def _valuate_subscription(self, state: UserState, sub: dict,
                               start: date, end: date) -> list[dict]:
        rng = self.rng
        w = SUBSCRIPTION_VALUE_WEIGHTS
        valuations = []

        started_on = sub.get("started_on") or start
        lifecycle_end = sub.get("cancelled_on") or end
        if lifecycle_end < started_on:
            return valuations

        current = max(start, started_on)
        while current <= lifecycle_end:
            period_start = current
            period_end = min(
                current + timedelta(days=89),
                lifecycle_end,
            )

            usage = clip01(float(rng.beta(2, 3)) * (1 + (sub.get("usage_frequency", 3) / 7.0)))
            price = float(sub.get("price", 10))
            total_cost = self._period_cost(sub, period_start, period_end)
            total_cost_float = float(total_cost)
            monthly_income = max(1, float(state.monthly_income))

            fit = clip01(
                state.quality_preference * 0.4
                + (1 - state.credit_stress) * 0.3
                + float(rng.normal(0, 0.1))
            )
            habit = clip01(usage * 0.7 + 0.3 * float(rng.beta(3, 2)))
            friction = clip01(
                (1 - usage) * 0.5
                + state.regret_sensitivity * 0.2
                + float(rng.normal(0, 0.1))
            )
            monthly_cost_equivalent = max(0.01, self._monthly_subscription_cost(sub))
            cost_ratio = min(1.0, monthly_cost_equivalent / (monthly_income * 0.05))

            raw_value = (
                w["w_usage"] * usage
                + w["w_fit"] * fit
                + w["w_habit"] * habit
                - w["w_friction"] * friction
                - w["w_cost"] * cost_ratio
            )

            noise = float(rng.normal(0, 0.05))
            estimated_value = Decimal(str(round(
                max(0, (0.5 + raw_value + noise) * total_cost_float), 2
            )))
            net_value = estimated_value - total_cost

            # Section 15.6: Recommendation mapping
            net_ratio = float(net_value) / max(0.01, float(total_cost))
            if net_ratio > SUBSCRIPTION_REC_THRESHOLDS["keep"]:
                rec = "buy"  # "keep" maps to buy in Recommendation choices
            elif net_ratio < SUBSCRIPTION_REC_THRESHOLDS["cancel"]:
                rec = "skip"  # "cancel" maps to skip
            else:
                rec = "wait"  # "downgrade"

            # Confidence (Section 15.7)
            signal_count = 3 + int(usage > 0.3) + int(habit > 0.5)
            ambiguity = abs(net_ratio)
            confidence = clip01(logistic_simple(
                0.5 + 0.2 * signal_count - 0.3 * (1 - ambiguity)
            ))

            # personal_value_score 0-150: usage + fit + habit - friction
            usage_fit = (usage + fit) / 2.0
            habit_friction = habit - friction
            combo = clip01(0.5 + 0.5 * (usage_fit + habit_friction) / 2)
            sb = VALUATION_SCORE_BANDS
            if combo > 0.75:  # extremely useful
                sub_score = int(rng.integers(sb["extremely_useful_min"], sb["extremely_useful_max"] + 1))
            elif combo > 0.45:  # match value
                sub_score = int(rng.integers(sb["match_lo"], sb["match_hi"] + 1))
            else:  # underused
                sub_score = int(rng.integers(20, sb["underused_max"] + 1))
            # Add noise: ±8 points so adjacent valuations vary
            sub_score = int(max(0, min(150, sub_score + int(rng.normal(0, 8)))))

            explanation = generate_explanation_json(rng, "subscription", {
                "usage": usage, "fit": fit, "habit": habit,
                "friction": friction, "cost_ratio": cost_ratio,
            })

            valuations.append({
                "period_start": period_start,
                "period_end": period_end,
                "total_cost": total_cost,
                "estimated_value": estimated_value,
                "net_value": net_value,
                "personal_value_score": sub_score,
                "recommendation": rec,
                "confidence": round(confidence, 2),
                "explanation_json": explanation,
                "evidence_json": {
                    "usage_frequency": round(usage, 3),
                    "fit_score": round(fit, 3),
                    "habit_score": round(habit, 3),
                    "friction_score": round(friction, 3),
                    "cost_ratio": round(cost_ratio, 3),
                    "billing_cycle": sub.get("billing_cycle", "monthly"),
                },
                "context": (
                    "subscription_cancel"
                    if sub.get("status") == "canceled" and period_end == lifecycle_end
                    else "subscription_renewal"
                ),
                "_sub_ref": sub,
            })

            current = period_end + timedelta(days=1)

        return valuations

    def _monthly_subscription_cost(self, sub: dict) -> float:
        price = float(sub.get("price", 0) or 0)
        billing_cycle = sub.get("billing_cycle", "monthly")
        if billing_cycle == "yearly":
            return price / 12.0
        if billing_cycle == "weekly":
            return price * 52.0 / 12.0
        return price

    def _period_cost(self, sub: dict, period_start: date, period_end: date) -> Decimal:
        price = float(sub.get("price", 0) or 0)
        billing_cycle = sub.get("billing_cycle", "monthly")
        days_in_period = max(1, (period_end - period_start).days + 1)
        if billing_cycle == "yearly":
            daily_cost = price / 365.0
        elif billing_cycle == "weekly":
            daily_cost = price / 7.0
        else:
            daily_cost = price / 30.0
        return Decimal(str(round(daily_cost * days_in_period, 2)))

    def _valuate_spend_transaction(self, state: UserState, txn: dict) -> dict | None:
        """Create ItemValuation for a spend transaction using merchant + behavioral signals."""
        rng = self.rng
        merch = txn.get("merchant_info", {})
        if not merch:
            return None
        item_name = merch.get("name", "Unknown")
        item_cat = merch.get("category", "other")
        amount = float(txn.get("amount", 50))
        usage_freq = txn.get("usage_frequency") or 2
        satisfaction = txn.get("satisfaction_rating") or 5
        regret = txn.get("regret_score") or 0.3

        # usage proxy: usage_freq/7 and satisfaction/10
        usage_proxy = clip01(usage_freq / 7.0 * 0.5 + satisfaction / 10.0 * 0.5)
        fit_proxy = clip01(1.0 - regret + float(rng.normal(0, 0.1)))
        combo = (usage_proxy + fit_proxy) / 2.0

        sb = VALUATION_SCORE_BANDS
        if combo > 0.75:
            score = int(rng.integers(sb["extremely_useful_min"], sb["extremely_useful_max"] + 1))
        elif combo > 0.45:
            score = int(rng.integers(sb["match_lo"], sb["match_hi"] + 1))
        else:
            score = int(rng.integers(20, sb["underused_max"] + 1))
        # Add noise: ±10 points for spend transactions
        score = int(max(0, min(150, score + int(rng.normal(0, 10)))))

        if score >= ITEM_REC_THRESHOLDS["buy"]:
            rec = "buy"
        elif score >= ITEM_REC_THRESHOLDS["wait"]:
            rec = "wait"
        else:
            rec = "skip"

        fair_price = Decimal(str(round(amount * 0.9, 2)))
        observed_price = Decimal(str(round(amount, 2)))
        confidence = clip01(0.6 + 0.2 * (1 if txn.get("reflection_text") else 0) + float(rng.normal(0, 0.05)))
        explanation = generate_explanation_json(rng, "item", {
            "score": score, "usage_proxy": round(usage_proxy, 3),
            "fit_proxy": round(fit_proxy, 3),
        })
        return {
            "item_name": item_name,
            "item_category": item_cat,
            "observed_price": observed_price,
            "estimated_fair_price": fair_price,
            "personal_value_score": score,
            "recommendation": rec,
            "confidence": round(confidence, 2),
            "evidence_json": {
                "usage_proxy": round(usage_proxy, 3),
                "fit_proxy": round(fit_proxy, 3),
            },
            "reasoning_json": explanation,
            "context": "one_off_purchase",
        }

    def _valuate_item(self, state: UserState) -> dict:
        """Create ItemValuation from ITEM_POOL with score bands 0-150."""
        rng = self.rng
        w = ITEM_VALUE_WEIGHTS

        item_name, item_cat, price_lo, price_hi = ITEM_POOL[
            int(rng.integers(0, len(ITEM_POOL)))
        ]

        observed_price = Decimal(str(round(float(rng.uniform(price_lo, price_hi)), 2)))
        fair_price = Decimal(str(round((price_lo + price_hi) / 2, 2)))

        quality_fit = clip01(
            state.quality_preference * 0.5
            + float(rng.beta(3, 3))
        )
        price_fairness = clip01(
            1 - abs(float(observed_price - fair_price)) / max(1, float(fair_price))
        )
        need_fit = clip01(float(rng.beta(2, 3)) + state.household_pressure * 0.2)
        budget_strain = clip01(
            float(observed_price) / max(1, float(state.monthly_income) * 0.1)
        )

        usage_proxy = (quality_fit + need_fit) / 2.0
        fit_proxy = (quality_fit + price_fairness - budget_strain * 0.5)
        combo = clip01((usage_proxy + fit_proxy) / 2.0)

        sb = VALUATION_SCORE_BANDS
        if combo > 0.75:
            personal_value_score = int(rng.integers(sb["extremely_useful_min"], sb["extremely_useful_max"] + 1))
        elif combo > 0.45:
            personal_value_score = int(rng.integers(sb["match_lo"], sb["match_hi"] + 1))
        else:
            personal_value_score = int(rng.integers(20, sb["underused_max"] + 1))
        # Add noise: ±10 points for ITEM_POOL valuations
        personal_value_score = int(max(0, min(150, personal_value_score + int(rng.normal(0, 10)))))

        if personal_value_score >= ITEM_REC_THRESHOLDS["buy"]:
            rec = "buy"
        elif personal_value_score >= ITEM_REC_THRESHOLDS["wait"]:
            rec = "wait"
        else:
            rec = "skip"

        signal_count = 3
        confidence = clip01(0.5 + 0.1 * signal_count + float(rng.normal(0, 0.05)))

        explanation = generate_explanation_json(rng, "item", {
            "score": personal_value_score,
            "quality_fit": quality_fit,
            "price_fairness": price_fairness,
            "need_fit": need_fit,
            "budget_strain": budget_strain,
        })

        return {
            "item_name": item_name,
            "item_category": item_cat,
            "observed_price": observed_price,
            "estimated_fair_price": fair_price,
            "personal_value_score": personal_value_score,
            "recommendation": rec,
            "confidence": round(confidence, 2),
            "evidence_json": {
                "quality_fit": round(quality_fit, 3),
                "price_fairness": round(price_fairness, 3),
                "need_fit": round(need_fit, 3),
                "budget_strain": round(budget_strain, 3),
            },
            "reasoning_json": explanation,
            "context": "one_off_purchase",
        }


def logistic_simple(x: float) -> float:
    import math
    return 1.0 / (1.0 + math.exp(-x))
