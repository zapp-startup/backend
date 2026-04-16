"""
Agent 9: Valuation Agent (Section 15)
Creates synthetic ground-truth valuation outputs for items and subscriptions.
Probabilistic buy/wait/skip mapping on 0–150 score space.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import numpy as np

from datagen.agents.base import BaseAgent
from datagen.config import (
    SUBSCRIPTION_COST_BENEFIT_ADJUSTMENTS,
    SUBSCRIPTION_MERCHANT_PRICE_TIERS,
    SUBSCRIPTION_PRICE_LOGNORMAL,
    SUBSCRIPTION_UTILIZATION_BURDEN_SENSITIVITY,
    SUBSCRIPTION_UTILIZATION_CATEGORY_FLOORS,
    SUBSCRIPTION_UTILIZATION_CATEGORY_SOFT_CAPS,
    SUBSCRIPTION_UTILIZATION_SOFT_CAP,
    SUBSCRIPTION_UTILIZATION_STATUS_MULTIPLIERS,
    SUBSCRIPTION_UTILIZATION_WEIGHTS,
    SUBSCRIPTION_VALUE_WEIGHTS,
    VALUATION_SCORE_BANDS,
)
from datagen.distributions import clip01
from datagen.export_utils import compute_subscription_cost_benefit
from datagen.state import UserState
from datagen.text import generate_explanation_json

ITEM_POOL = [
    ("AirPods Pro", "electronics", 180, 250, False),
    ("MacBook Air", "electronics", 900, 1300, False),
    ("iPad", "electronics", 350, 600, False),
    ("Running Shoes", "fitness", 80, 180, False),
    ("Protein Powder", "fitness", 25, 60, False),
    ("Yoga Mat", "fitness", 20, 80, False),
    ("Textbook", "education", 30, 120, False),
    ("Online Course Bundle", "education", 50, 200, False),
    ("Coffee Maker", "home", 40, 200, False),
    ("Office Chair", "home", 150, 500, False),
    ("Monitor", "electronics", 200, 600, False),
    ("Winter Jacket", "other", 80, 300, False),
    ("Backpack", "other", 40, 120, False),
    ("Noise Cancelling Headphones", "electronics", 150, 350, False),
    ("Standing Desk", "home", 250, 700, False),
    ("Instant Pot", "home", 60, 150, False),
    ("Skincare Set", "health", 30, 100, False),
    ("Vitamins Bundle", "health", 20, 60, False),
    ("Designer Handbag", "luxury", 1800, 4200, True),
    ("Swiss Watch", "luxury", 2600, 9000, True),
    ("Luxury Sneakers", "luxury", 550, 1400, True),
    ("Cashmere Coat", "luxury", 900, 2800, True),
]

ITEM_PRICE_PROFILES: dict[str, tuple[float, float]] = {
    "electronics": (0.02, 0.12),
    "fitness": (-0.01, 0.10),
    "education": (-0.03, 0.11),
    "home": (0.01, 0.13),
    "health": (-0.02, 0.10),
    "luxury": (0.12, 0.18),
    "other": (0.00, 0.14),
}

MERCHANT_FAMILY_PRICE_PROFILES: dict[str, tuple[float, float]] = {
    "ecommerce": (0.03, 0.12),
    "food_quick": (-0.02, 0.08),
    "delivery_membership": (0.07, 0.10),
    "grocery_retail": (-0.01, 0.07),
    "pharmacy": (0.02, 0.09),
    "rideshare": (0.05, 0.10),
    "fuel": (0.01, 0.06),
    "entertainment_out": (0.06, 0.13),
    "luxury_retail": (0.18, 0.18),
}


def _income_tier(monthly_income: float) -> str:
    annual = monthly_income * 12
    if annual < 25000:
        return "low"
    if annual < 50000:
        return "moderate"
    return "high"


def _softmax_choice(rng, logits: list[float], labels: tuple[str, ...]) -> str:
    x = np.array(logits, dtype=float) + rng.normal(0, 0.4, size=len(logits))
    x = x - np.max(x)
    e = np.exp(x)
    p = e / e.sum()
    i = int(rng.choice(len(labels), p=p))
    return labels[i]


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

        for sub in subscriptions:
            vals = self._valuate_subscription(state, sub, start_date, end_date)
            sub_valuations.extend(vals)

        for txn in spend_txns:
            if rng.random() < 0.90:
                val = self._valuate_spend_transaction(state, txn)
                if val:
                    item_valuations.append(val)

        n_extra = max(0, int(rng.poisson(2)))
        for _ in range(n_extra):
            item_valuations.append(self._valuate_item(state))

        return {
            "subscription_valuations": sub_valuations,
            "item_valuations": item_valuations,
        }

    @staticmethod
    def _monthly_subscription_cost(sub: dict) -> float:
        price = float(sub.get("price", 0) or 0)
        billing_cycle = sub.get("billing_cycle", "monthly")
        if billing_cycle == "yearly":
            return price / 12.0
        if billing_cycle == "weekly":
            return price * 52.0 / 12.0
        return price

    @staticmethod
    def _subscription_category_key(sub: dict) -> str:
        merch = sub.get("merchant_info") or {}
        for raw_key in (
            merch.get("merchant_family"),
            merch.get("category"),
            sub.get("category"),
        ):
            if not raw_key:
                continue
            key = str(raw_key)
            if key == "delivery_membership":
                return "food_delivery"
            return key
        return "default"

    def _expected_subscription_monthly_cost(self, sub: dict, category_key: str) -> float:
        merch = sub.get("merchant_info") or {}
        tiers = SUBSCRIPTION_MERCHANT_PRICE_TIERS.get(merch.get("name", ""))
        if tiers:
            total_weight = sum(weight for _price_s, weight in tiers) or 1.0
            return sum(float(price_s) * weight for price_s, weight in tiers) / total_weight

        mu, sig = SUBSCRIPTION_PRICE_LOGNORMAL.get(
            category_key,
            SUBSCRIPTION_PRICE_LOGNORMAL["streaming"],
        )
        return float(np.exp(mu + (sig ** 2) / 2.0))

    @staticmethod
    def _soft_cap_unit_interval(value: float, *, status: str, category_key: str) -> float:
        cfg = SUBSCRIPTION_UTILIZATION_SOFT_CAP
        capped = max(0.0, float(value))
        soft_start = cfg["start"]
        if capped > soft_start:
            capped = soft_start + (capped - soft_start) * cfg["slope"]

        status_cap = cfg.get(f"max_{status}", cfg["max_active"])
        category_cap = SUBSCRIPTION_UTILIZATION_CATEGORY_SOFT_CAPS.get(category_key, 1.0)
        return round(max(0.0, min(1.0, capped, status_cap, category_cap)), 4)

    def _compute_subscription_utilization(
        self,
        *,
        sub: dict,
        usage: float,
        usage_frequency: float,
        fit_signal: float,
        friction_signal: float,
        support_confidence: float,
        monthly_cost_equivalent: float,
        cost_share: float,
    ) -> dict[str, float | None | str]:
        weights = SUBSCRIPTION_UTILIZATION_WEIGHTS
        status = str(sub.get("status", "active"))
        category_key = self._subscription_category_key(sub)
        status_multiplier = SUBSCRIPTION_UTILIZATION_STATUS_MULTIPLIERS.get(status, 1.0)
        burden_sensitivity = SUBSCRIPTION_UTILIZATION_BURDEN_SENSITIVITY.get(
            category_key,
            SUBSCRIPTION_UTILIZATION_BURDEN_SENSITIVITY["default"],
        )
        feedback_value = sub.get("feedback_value_score")
        feedback_confidence = sub.get("feedback_confidence")

        usage_score = clip01(0.72 * usage + 0.28 * clip01(float(usage_frequency)))
        category_floor_base = SUBSCRIPTION_UTILIZATION_CATEGORY_FLOORS.get(
            category_key,
            SUBSCRIPTION_UTILIZATION_CATEGORY_FLOORS["default"],
        )
        if status == "paused":
            category_floor_base *= 0.60
        elif status == "canceled":
            category_floor_base *= 0.25
        category_floor_component = weights["category_floor_weight"] * category_floor_base

        feedback_bonus = 0.0
        if feedback_value is not None and feedback_confidence is not None:
            feedback_bonus = (
                weights["feedback_bonus_weight"]
                * max(0.0, float(feedback_value) - 0.55)
                * max(0.0, float(feedback_confidence))
            )

        expected_monthly_cost = max(0.01, self._expected_subscription_monthly_cost(sub, category_key))
        price_ratio = monthly_cost_equivalent / expected_monthly_cost
        price_penalty = (
            weights["price_penalty_weight"]
            * burden_sensitivity
            * max(0.0, price_ratio - 1.05)
            * min(1.35, 0.65 + cost_share * 6.0)
        )
        friction_penalty = weights["friction_penalty_weight"] * max(0.0, friction_signal - 0.38)
        fit_penalty = weights["fit_penalty_weight"] * max(0.0, 0.60 - fit_signal)

        confidence_penalty = 0.0
        if feedback_confidence is not None:
            confidence_penalty += (
                weights["confidence_penalty_weight"]
                * max(0.0, 0.62 - float(feedback_confidence))
                * 0.6
            )
        confidence_penalty += (
            weights["confidence_penalty_weight"]
            * max(0.0, 0.58 - support_confidence)
        )

        util_raw = (
            weights["usage_weight"] * usage_score
            + category_floor_component
            + feedback_bonus
        )
        util_adjusted = (
            util_raw * status_multiplier
            - price_penalty
            - friction_penalty
            - fit_penalty
            - confidence_penalty
        )
        util_final = self._soft_cap_unit_interval(
            util_adjusted,
            status=status,
            category_key=category_key,
        )

        return {
            "category_key": category_key,
            "input_usage_score": round(usage_score, 4),
            "input_status_multiplier": round(status_multiplier, 4),
            "input_price_ratio": round(price_ratio, 4),
            "input_feedback_value": None if feedback_value is None else round(float(feedback_value), 4),
            "input_feedback_confidence": None if feedback_confidence is None else round(float(feedback_confidence), 4),
            "penalty_price": round(price_penalty, 4),
            "penalty_friction": round(friction_penalty, 4),
            "penalty_fit": round(fit_penalty, 4),
            "penalty_confidence": round(confidence_penalty, 4),
            "utilization_raw": round(util_raw, 4),
            "utilization_final": util_final,
        }

    def _compute_adjusted_subscription_cost_benefit(
        self,
        *,
        estimated_value: Decimal,
        total_cost: Decimal,
        status: str,
        category_key: str,
        price_ratio: float,
    ) -> tuple[float | None, float | None]:
        raw = compute_subscription_cost_benefit(estimated_value, total_cost)
        if raw is None:
            return None, None

        cfg = SUBSCRIPTION_COST_BENEFIT_ADJUSTMENTS
        burden_sensitivity = SUBSCRIPTION_UTILIZATION_BURDEN_SENSITIVITY.get(
            category_key,
            SUBSCRIPTION_UTILIZATION_BURDEN_SENSITIVITY["default"],
        )
        adjustment_penalty = cfg["status_penalty"].get(status, 0.0)
        adjustment_penalty += (
            cfg["price_penalty_weight"]
            * max(0.0, price_ratio - 1.10)
            * burden_sensitivity
            * cfg["nonessential_multiplier"]
        )

        final = compute_subscription_cost_benefit(
            estimated_value,
            total_cost,
            adjustment_penalty=adjustment_penalty,
            spread_gain=cfg["spread_gain"],
            spread_center=cfg["spread_center"],
        )
        return raw, final

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

            usage_frequency = float(sub.get("usage_frequency", 0) or 0)
            usage_frequency = max(0.0, min(1.0, usage_frequency))
            usage = clip01(float(rng.beta(2, 3)) * (1.0 + usage_frequency))
            total_cost = self._period_cost(sub, period_start, period_end)
            total_cost_float = float(total_cost)
            monthly_income = max(1, float(state.monthly_income))

            fit_signal = clip01(
                state.quality_preference * 0.4
                + (1 - state.credit_stress) * 0.3
                + usage * 0.15
                + float(rng.normal(0, 0.1))
            )
            habit = clip01(usage * 0.7 + 0.3 * float(rng.beta(3, 2)))
            monthly_cost_equivalent = max(0.01, self._monthly_subscription_cost(sub))
            cost_share = monthly_cost_equivalent / monthly_income
            friction_signal = clip01(
                (1 - usage) * 0.35
                + state.regret_sensitivity * 0.2
                + min(0.2, cost_share * 1.4)
                + float(rng.normal(0, 0.1))
            )
            cost_ratio = min(1.0, monthly_cost_equivalent / (monthly_income * 0.05))
            support_confidence = clip01(
                0.35
                + 0.18 * int(usage > 0.3)
                + 0.15 * int(usage_frequency >= (3.0 / 7.0))
                + 0.22 * fit_signal
                - 0.18 * friction_signal
            )
            utilization_debug = self._compute_subscription_utilization(
                sub=sub,
                usage=usage,
                usage_frequency=usage_frequency,
                fit_signal=fit_signal,
                friction_signal=friction_signal,
                support_confidence=support_confidence,
                monthly_cost_equivalent=monthly_cost_equivalent,
                cost_share=cost_share,
            )
            subscription_utilization = float(utilization_debug["utilization_final"])

            raw_value = (
                w["w_usage"] * subscription_utilization
                + w["w_fit"] * fit_signal
                + w["w_habit"] * habit
                - w["w_friction"] * friction_signal
                - w["w_cost"] * cost_ratio
            )
            contradiction_drag = (
                max(0.0, subscription_utilization - 0.65)
                * (
                    0.18 * max(0.0, float(utilization_debug["input_price_ratio"]) - 1.0)
                    + 0.12 * max(0.0, friction_signal - 0.45)
                    + 0.10 * max(0.0, 0.58 - fit_signal)
                )
            )

            noise = float(rng.normal(0, 0.05))
            estimated_value = Decimal(str(round(
                max(0, (0.38 + raw_value + noise + subscription_utilization * 0.30 - contradiction_drag) * total_cost_float), 2
            )))
            net_value = estimated_value - total_cost

            rec = self._subscription_recommendation(
                state=state,
                net_value=net_value,
                usage=subscription_utilization,
                cost_share=cost_share,
                monthly_income=monthly_income,
                fit=fit_signal,
                friction=friction_signal,
            )

            signal_count = 3 + int(usage > 0.3) + int(habit > 0.5)
            net_ratio = float(net_value) / max(0.01, float(total_cost))
            ambiguity = abs(net_ratio)
            confidence = clip01(
                0.55 * logistic_simple(0.5 + 0.2 * signal_count - 0.3 * (1 - ambiguity))
                + 0.45 * support_confidence
            )
            cost_benefit_raw, subscription_cost_benefit = self._compute_adjusted_subscription_cost_benefit(
                estimated_value=estimated_value,
                total_cost=total_cost,
                status=str(sub.get("status", "active")),
                category_key=str(utilization_debug["category_key"]),
                price_ratio=float(utilization_debug["input_price_ratio"]),
            )

            usage_fit = (subscription_utilization + fit_signal) / 2.0
            habit_friction = habit - friction_signal
            combo = clip01(0.5 + 0.5 * (usage_fit + habit_friction) / 2)
            sb = VALUATION_SCORE_BANDS
            if combo > 0.75:
                sub_score = int(rng.integers(sb["extremely_useful_min"], sb["extremely_useful_max"] + 1))
            elif combo > 0.45:
                sub_score = int(rng.integers(sb["match_lo"], sb["match_hi"] + 1))
            else:
                sub_score = int(rng.integers(20, sb["underused_max"] + 1))
            sub_score = int(max(0, min(150, sub_score + int(rng.normal(0, 8)))))

            explanation = generate_explanation_json(rng, "subscription", {
                "usage": subscription_utilization, "fit": fit_signal, "habit": habit,
                "friction": friction_signal, "cost_ratio": cost_ratio,
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
                "subscription_utilization": subscription_utilization,
                "subscription_cost_benefit": subscription_cost_benefit,
                "explanation_json": explanation,
                "evidence_json": {
                    "usage_frequency": usage_frequency,
                    "subscription_utilization": subscription_utilization,
                    "subscription_cost_benefit": subscription_cost_benefit,
                    "fit_score": round(fit_signal, 3),
                    "habit_score": round(habit, 3),
                    "friction_score": round(friction_signal, 3),
                    "cost_ratio": round(cost_ratio, 3),
                    "billing_cycle": sub.get("billing_cycle", "monthly"),
                    "input_usage_score": utilization_debug["input_usage_score"],
                    "input_status_multiplier": utilization_debug["input_status_multiplier"],
                    "input_price_ratio": utilization_debug["input_price_ratio"],
                    "input_feedback_value": utilization_debug["input_feedback_value"],
                    "input_feedback_confidence": utilization_debug["input_feedback_confidence"],
                    "penalty_price": utilization_debug["penalty_price"],
                    "penalty_friction": utilization_debug["penalty_friction"],
                    "penalty_fit": utilization_debug["penalty_fit"],
                    "penalty_confidence": utilization_debug["penalty_confidence"],
                    "utilization_raw": utilization_debug["utilization_raw"],
                    "utilization_final": utilization_debug["utilization_final"],
                    "cost_benefit_raw": cost_benefit_raw,
                    "cost_benefit_final": subscription_cost_benefit,
                },
                "context": (
                    "subscription_cancel"
                    if sub.get("status") == "canceled" and period_end == lifecycle_end
                    else "subscription_renewal"
                ),
                "_sub_ref": sub,
                "_sub_key": sub.get("_sub_key"),
            })

            current = period_end + timedelta(days=1)

        return valuations

    def _subscription_recommendation(
        self,
        *,
        state: UserState,
        net_value: Decimal,
        usage: float,
        cost_share: float,
        monthly_income: float,
        fit: float,
        friction: float,
    ) -> str:
        """
        Probabilistic buy / wait / skip from net value, usage, burden, liquidity, income.
        buy ~= keep; strongly negative net value rarely yields buy.
        """
        rng = self.rng
        net_f = float(net_value)
        liq_n = state.liquidity_numeric()
        burden_shift = {
            "light": 0.0,
            "normal": 0.35,
            "stretched": 1.25,
            "overloaded": 2.1,
        }.get(state.subscription_burden_state, 0.35)

        income_rel = min(1.5, monthly_income / max(1.0, 4000.0))

        buy = (
            1.30 * usage
            + 0.45 * fit
            - 0.85 * friction
            - 8.5 * cost_share
            - burden_shift
            - 1.1 * liq_n
            + 0.25 * income_rel
        )
        if net_f < -15:
            buy -= 10.0 + 0.05 * abs(net_f + 15)
        elif net_f < -10:
            buy -= 5.5
            if usage < 0.48:
                buy -= 3.2

        wait = (
            -0.12 * net_f / max(8.0, abs(net_f) * 0.15 + 8.0)
            - 0.32 * abs(usage - 0.42)
            - 0.18 * max(0.0, usage - 0.58)
            + 0.15 * friction
            + float(rng.normal(0, 0.08))
        )

        skip = (
            -0.55 * usage
            - 0.35 * fit
            + 0.55 * friction
            + 6.0 * cost_share
            + burden_shift * 0.9
            + 0.9 * liq_n
        )
        if net_f < -10 and usage < 0.52:
            skip += 2.8
        if net_f < -15:
            skip += 1.8

        logits = [
            buy + float(rng.normal(0, 0.32)),
            wait + float(rng.normal(0, 0.26)),
            skip + float(rng.normal(0, 0.32)),
        ]
        return _softmax_choice(rng, logits, ("buy", "wait", "skip"))

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

    def _item_recommendation_from_score(
        self,
        state: UserState,
        score: int,
        price_fairness: float,
        need_fit: float,
        luxury_signal: float = 0.0,
    ) -> str:
        rng = self.rng
        mi = float(state.monthly_income or 1)
        tier = _income_tier(mi)
        buy_logit = (score - 90) / 25.0
        skip_logit = (65 - score) / 25.0
        wait_logit = 0.0
        buy_logit -= state.price_sensitivity * (1.0 - price_fairness) * 1.5
        buy_logit += luxury_signal * (0.45 * state.luxury_affinity + 0.18 * state.quality_preference)
        skip_logit += luxury_signal * (0.55 * state.price_sensitivity + 0.50 * state.credit_stress)
        if tier in ("low", "moderate") and price_fairness < 0.5 and need_fit < 0.6:
            buy_logit -= (0.5 - price_fairness) * 1.5
        if luxury_signal > 0 and state.liquidity in ("tight", "overdraft_risk", "overdrafted"):
            buy_logit -= 1.15 * luxury_signal
            skip_logit += 1.2 * luxury_signal
        if luxury_signal > 0 and state.credit_stress >= 0.6:
            buy_logit -= 0.45 * luxury_signal
            wait_logit += 0.18 * luxury_signal
            skip_logit += 0.22 * luxury_signal
        return _softmax_choice(rng, [buy_logit, wait_logit, skip_logit], ("buy", "wait", "skip"))

    def _sample_fair_price(
        self,
        *,
        observed_amount: float,
        item_category: str,
        merchant_family: str | None = None,
        state: UserState,
        luxury_signal: float = 0.0,
    ) -> tuple[Decimal, float]:
        rng = self.rng
        base_mean, base_sigma = ITEM_PRICE_PROFILES.get(item_category, ITEM_PRICE_PROFILES["other"])
        fam_mean, fam_sigma = MERCHANT_FAMILY_PRICE_PROFILES.get(
            merchant_family or "",
            (0.0, 0.0),
        )
        premium_mean = base_mean + fam_mean
        premium_sigma = max(base_sigma, fam_sigma, 0.06)
        premium_mean += (state.quality_preference - 0.5) * 0.10
        premium_mean -= (state.price_sensitivity - 0.5) * 0.14
        premium_mean += luxury_signal * (0.12 + 0.14 * state.luxury_affinity)
        premium_sigma += luxury_signal * 0.03
        premium = float(rng.normal(premium_mean, premium_sigma))
        premium = max(-0.22, min(0.48 if luxury_signal > 0 else 0.32, premium))
        fair_amount = observed_amount / max(0.55, 1.0 + premium)
        fair_amount *= float(rng.uniform(0.97, 1.03))
        fair_amount = max(0.5, fair_amount)
        observed_price = Decimal(str(round(observed_amount, 2)))
        fair_price = Decimal(str(round(fair_amount, 2)))
        spread = abs(float(observed_price - fair_price)) / max(1.0, max(float(observed_price), float(fair_price)))
        price_fairness = clip01(1.0 - 1.55 * spread)
        return fair_price, price_fairness

    def _valuate_spend_transaction(self, state: UserState, txn: dict) -> dict | None:
        rng = self.rng
        merch = txn.get("merchant_info", {})
        if not merch:
            return None
        item_name = merch.get("name", "Unknown")
        item_cat = "luxury" if merch.get("merchant_family") == "luxury_retail" else merch.get("category", "other")
        merchant_family = merch.get("merchant_family")
        luxury_signal = 1.0 if merchant_family == "luxury_retail" or item_cat == "luxury" else 0.0
        amount = float(txn.get("amount", 50))
        usage_freq = txn.get("usage_frequency") or 2
        satisfaction = txn.get("satisfaction_rating") or 5
        regret = txn.get("regret_score") or 0.3

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
        score = int(max(0, min(150, score + int(rng.normal(0, 10)))))

        fair_price, price_fairness = self._sample_fair_price(
            observed_amount=amount,
            item_category=item_cat,
            merchant_family=merchant_family,
            state=state,
            luxury_signal=luxury_signal,
        )
        need_fit = fit_proxy
        rec = self._item_recommendation_from_score(
            state, score, price_fairness, need_fit, luxury_signal=luxury_signal,
        )

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
                "price_fairness": round(price_fairness, 3),
            },
            "reasoning_json": explanation,
            "context": "one_off_purchase",
        }

    def _valuate_item(self, state: UserState) -> dict:
        rng = self.rng

        item_name, item_cat, price_lo, price_hi, is_luxury = ITEM_POOL[
            int(rng.integers(0, len(ITEM_POOL)))
        ]
        luxury_signal = 1.0 if is_luxury else 0.0

        anchor = float(rng.uniform(price_lo, price_hi))
        fair_price, _ = self._sample_fair_price(
            observed_amount=anchor,
            item_category=item_cat,
            merchant_family=None,
            state=state,
            luxury_signal=luxury_signal,
        )
        obs_multiplier = float(rng.normal(
            1.0 + (state.quality_preference - state.price_sensitivity) * 0.06 + luxury_signal * (0.05 + 0.07 * state.luxury_affinity),
            0.11 + luxury_signal * 0.04,
        ))
        obs_multiplier = max(0.72, min(1.46 if luxury_signal > 0 else 1.34, obs_multiplier))
        observed_price = Decimal(str(round(max(price_lo * 0.85, float(fair_price) * obs_multiplier), 2)))

        quality_fit = clip01(
            state.quality_preference * 0.5
            + float(rng.beta(3, 3))
        )
        price_fairness = clip01(
            1 - 1.45 * abs(float(observed_price - fair_price)) / max(1, max(float(fair_price), float(observed_price)))
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
        personal_value_score = int(max(0, min(150, personal_value_score + int(rng.normal(0, 10)))))

        rec = self._item_recommendation_from_score(
            state, personal_value_score, price_fairness, need_fit, luxury_signal=luxury_signal,
        )

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
                "luxury_signal": round(luxury_signal, 3),
            },
            "reasoning_json": explanation,
            "context": "one_off_purchase",
        }


def logistic_simple(x: float) -> float:
    import math
    return 1.0 / (1.0 + math.exp(-x))
