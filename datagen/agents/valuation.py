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
        start_date: date = context["start_date"]
        end_date: date = context["end_date"]

        sub_valuations = []
        item_valuations = []

        # Subscription valuations (Section 15.4)
        for sub in subscriptions:
            vals = self._valuate_subscription(state, sub, start_date, end_date)
            sub_valuations.extend(vals)

        # Item valuations (Section 15.5)
        n_items = max(1, int(rng.poisson(5)))
        for _ in range(n_items):
            val = self._valuate_item(state)
            item_valuations.append(val)

        return {
            "subscription_valuations": sub_valuations,
            "item_valuations": item_valuations,
        }

    def _valuate_subscription(self, state: UserState, sub: dict,
                               start: date, end: date) -> list[dict]:
        rng = self.rng
        w = SUBSCRIPTION_VALUE_WEIGHTS
        valuations = []

        # Generate one valuation per quarter
        current = date(start.year, start.month, 1)
        while current <= end:
            period_start = current
            period_end = min(
                date(current.year, current.month, 1) + timedelta(days=89),
                end,
            )

            usage = clip01(float(rng.beta(2, 3)) * (1 + (sub.get("usage_frequency", 3) / 7.0)))
            price = float(sub.get("price", 10))
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
            cost_ratio = min(1.0, price / (monthly_income * 0.05))

            raw_value = (
                w["w_usage"] * usage
                + w["w_fit"] * fit
                + w["w_habit"] * habit
                - w["w_friction"] * friction
                - w["w_cost"] * cost_ratio
            )

            noise = float(rng.normal(0, 0.05))
            estimated_value = Decimal(str(round(max(0, (raw_value + noise) * price * 3), 2)))
            total_cost = Decimal(str(round(price * 3, 2)))  # quarterly
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
                "recommendation": rec,
                "confidence": round(confidence, 2),
                "explanation_json": explanation,
                "evidence_json": {
                    "usage_frequency": round(usage, 3),
                    "fit_score": round(fit, 3),
                    "habit_score": round(habit, 3),
                    "friction_score": round(friction, 3),
                    "cost_ratio": round(cost_ratio, 3),
                },
                "context": "subscription_renewal",
                "_sub_ref": sub,
            })

            # Advance by ~3 months
            month = current.month + 3
            year = current.year
            if month > 12:
                month -= 12
                year += 1
            current = date(year, month, 1)

        return valuations

    def _valuate_item(self, state: UserState) -> dict:
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

        raw_score = (
            w["l_quality_fit"] * quality_fit
            + w["l_price_fairness"] * price_fairness
            + w["l_need_fit"] * need_fit
            - w["l_budget_strain"] * budget_strain
        )

        noise = float(rng.normal(0, 0.05))
        personal_value_score = int(max(0, min(100, (raw_score + noise) * 100)))

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
