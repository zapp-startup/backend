"""
Agent 6: Spend Agent (Section 12)
Generates discretionary spend events day-by-day using logistic purchase
probability, LogNormal amounts, and payday/weekend/seasonality multipliers.
Updates liquidity state via balance thresholds.
Writes to: transactions_transaction
"""

from __future__ import annotations

import math
from datetime import date, datetime, timedelta
from decimal import Decimal

from datagen.agents.base import BaseAgent
from datagen.config import AMOUNT_LOGNORMAL, SPEND_LOGISTIC
from datagen.distributions import (
    clip01,
    logistic,
    make_aware_dt,
    payday_multiplier,
    sample_lognormal,
    seasonality_multiplier,
    weekend_multiplier,
)
from datagen.state import UserState


DISCRETIONARY_CATEGORIES = ["groceries", "dining", "transport", "shopping",
                            "entertainment", "health", "travel"]


class SpendAgent(BaseAgent):
    """Generate discretionary spending transactions day by day."""

    def run(self, state: UserState, context: dict) -> dict:
        rng = self.rng
        start_date: date = context["start_date"]
        end_date: date = context["end_date"]
        merchant_agent = context.get("merchant_agent")
        merchant_catalog = context.get("merchant_catalog", [])

        transactions = []
        current = start_date

        while current <= end_date:
            # Precompute per-day signals once (avoid repeated days_since_payday/liquidity scans)
            days_since = state.days_since_payday(current)
            payday_window = 1.0 if days_since <= 3 else 0.0
            is_weekend = 1.0 if current.weekday() >= 5 else 0.0
            liq = state.liquidity_numeric()

            for cat in DISCRETIONARY_CATEGORIES:
                p = self._purchase_probability(
                    state, cat, is_weekend, payday_window, liq
                )

                if rng.random() < p:
                    amount = self._sample_amount(
                        state, current, cat, days_since, liq
                    )
                    merch = None
                    desc = cat.upper()

                    if merchant_agent and merchant_catalog:
                        merch = merchant_agent.pick_merchant_for_category(cat, merchant_catalog)
                        if merch:
                            desc = merchant_agent.render_description_raw(
                                merch["name"], merch.get("domain", "")
                            )

                    # Time of day: category-dependent
                    hour = self._sample_hour(cat)
                    minute = int(rng.integers(0, 60))
                    occurred = make_aware_dt(
                        datetime(current.year, current.month, current.day, hour, minute)
                    )

                    txn = {
                        "direction": "spend",
                        "amount": Decimal(str(round(amount, 2))),
                        "occurred_at": occurred,
                        "category": self._map_category(cat),
                        "payment_channel": self._pick_channel(cat),
                        "description_raw": desc,
                        "merchant_info": merch,
                        "subscription_obj": None,
                        "spend_category": cat,
                    }
                    transactions.append(txn)

                    state.balance_proxy -= txn["amount"]

            # Section 12.7: Update liquidity daily
            state.update_liquidity()

            # Overdraft suppression: if overdrafted, sharply reduce future spend
            current += timedelta(days=1)

        return {"spend_transactions": transactions}

    def _purchase_probability(
        self,
        state: UserState,
        category: str,
        is_weekend: float,
        payday_window: float,
        liq: float,
    ) -> float:
        """Section 12.5: Logistic purchase probability."""
        coeffs = SPEND_LOGISTIC.get(category)
        if not coeffs:
            return 0.02

        z = (coeffs["b0"]
             + coeffs["b_weekend"] * is_weekend
             + coeffs["b_payday"] * payday_window
             + coeffs["b_liq"] * liq
             + coeffs["b_impulse"] * state.impulse
             + coeffs["b_novelty"] * state.novelty_seeking)

        p = logistic(z)

        # Budget share scaling: categories with higher budget share get more txns
        budget_share = state.category_budgets.get(category, 0.05)
        p *= (0.5 + budget_share * 5)

        # Overdraft suppression (Section 12.4)
        if state.liquidity == "overdrafted" and state.impulse < 0.7:
            p *= 0.15
        elif state.liquidity == "overdraft_risk":
            p *= 0.5

        return clip01(p)

    def _sample_amount(
        self,
        state: UserState,
        day: date,
        category: str,
        days_since: int,
        liq: float,
    ) -> float:
        """Section 12.5: LogNormal amount with multipliers."""
        rng = self.rng
        mu, sigma = AMOUNT_LOGNORMAL.get(category, (3.0, 0.6))

        # Quality preference shifts mu
        mu += (state.quality_preference - 0.5) * 0.3

        # Liquidity shifts mu down
        mu -= liq * 0.2

        base_amount = sample_lognormal(rng, mu, sigma)

        # Payday multiplier (days_since precomputed)
        pm = payday_multiplier(days_since, state.payday_eta, state.payday_tau)

        # Weekend multiplier
        wm = weekend_multiplier(day, category)

        # Seasonality multiplier
        sm = seasonality_multiplier(day.month, category)

        return max(0.50, base_amount * pm * wm * sm)

    def _sample_hour(self, category: str) -> int:
        """Sample plausible hour of day for a purchase category."""
        rng = self.rng
        hour_ranges = {
            "groceries": (8, 20),
            "dining": (11, 22),
            "transport": (6, 22),
            "shopping": (9, 23),
            "entertainment": (10, 23),
            "health": (7, 18),
            "travel": (8, 22),
        }
        lo, hi = hour_ranges.get(category, (8, 22))
        return int(rng.integers(lo, hi))

    def _map_category(self, spend_cat: str) -> str:
        """Map spend category to TransactionCategory value."""
        cat_map = {
            "groceries": "groceries",
            "dining": "eating_out",
            "transport": "transport",
            "shopping": "shopping",
            "entertainment": "entertainment",
            "health": "health",
            "travel": "other",
        }
        return cat_map.get(spend_cat, "other")

    def _pick_channel(self, category: str) -> str:
        rng = self.rng
        if category in ("groceries", "dining"):
            return str(rng.choice(["card", "card", "card", "cash"]))
        if category in ("shopping", "entertainment", "travel"):
            return str(rng.choice(["card", "online", "online"]))
        return str(rng.choice(["card", "online", "bank"]))
