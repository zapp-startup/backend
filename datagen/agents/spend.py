"""
Agent 6: Spend Agent (Section 12)
Essential then discretionary passes per calendar month; logistic purchase
probability; LogNormal amounts; merchant-category compatibility.
Writes to: transactions_transaction
"""

from __future__ import annotations

from calendar import monthrange
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


ESSENTIAL_CATEGORIES = ["groceries", "transport"]
DISCRETIONARY_CATEGORIES = ["dining", "shopping", "entertainment", "health", "travel"]


def _monthly_subscription_cost_from_context(context: dict) -> float:
    total = 0.0
    for s in context.get("subscriptions", []):
        if s.get("status") != "active":
            continue
        price = float(s.get("price", 0) or 0)
        bc = s.get("billing_cycle", "monthly")
        if bc == "yearly":
            total += price / 12.0
        elif bc == "weekly":
            total += price * 52.0 / 12.0
        else:
            total += price
    return total


def _iter_month_segments(start_date: date, end_date: date):
    y, m = start_date.year, start_date.month
    while True:
        last_d = monthrange(y, m)[1]
        last = date(y, m, last_d)
        seg_start = max(start_date, date(y, m, 1))
        seg_end = min(end_date, last)
        if seg_start <= seg_end:
            yield (y, m), seg_start, seg_end
        if last >= end_date:
            break
        if m == 12:
            y, m = y + 1, 1
        else:
            m += 1


class SpendAgent(BaseAgent):
    """Generate spending: essential pass then discretionary pass per month."""

    def run(self, state: UserState, context: dict) -> dict:
        rng = self.rng
        start_date: date = context["start_date"]
        end_date: date = context["end_date"]
        merchant_agent = context.get("merchant_agent")
        merchant_catalog = context.get("merchant_catalog", [])

        transactions: list[dict] = []
        mi = float(state.monthly_income or 1)

        for _ym, seg_start, seg_end in _iter_month_segments(start_date, end_date):
            state.large_purchase_shock_this_month = False
            essential_month = 0.0

            # Pass 1: essential categories (utilities are in ObligationAgent / fixed_month)
            d = seg_start
            while d <= seg_end:
                essential_month += self._day_essential(
                    state, context, d, transactions, merchant_agent, merchant_catalog,
                )
                d += timedelta(days=1)

            sub_m = _monthly_subscription_cost_from_context(context)
            fixed_m = float(state.monthly_fixed_expenses)
            income_m = mi
            reserve = income_m * (0.05 + 0.05 * state.income_stability_numeric())
            debt_cap = state.debt_capacity_monthly()
            available = income_m - fixed_m - essential_month - sub_m + debt_cap
            disc_cap = max(0.0, available - reserve)
            month_disc_spent = 0.0

            # Pass 2: discretionary (capped)
            d = seg_start
            while d <= seg_end:
                month_disc_spent = self._day_discretionary(
                    state,
                    context,
                    d,
                    transactions,
                    merchant_agent,
                    merchant_catalog,
                    month_disc_spent,
                    disc_cap,
                    mi,
                )
                d += timedelta(days=1)

        return {"spend_transactions": transactions}

    def _day_essential(
        self,
        state: UserState,
        context: dict,
        current: date,
        transactions: list,
        merchant_agent,
        merchant_catalog: list,
    ) -> float:
        """Essential categories for one day; returns amount spent toward essential_month."""
        rng = self.rng
        day_total = 0.0
        days_since = state.days_since_payday(current)
        payday_window = 1.0 if days_since <= 3 else 0.0
        is_weekend = 1.0 if current.weekday() >= 5 else 0.0
        liq = state.liquidity_numeric()

        for cat in ESSENTIAL_CATEGORIES:
            p = self._purchase_probability(
                state, cat, is_weekend, payday_window, liq, essential=True,
            )
            if rng.random() >= p:
                continue
            amount = self._sample_amount(state, current, cat, days_since, liq)
            amount = self._attenuate_large_purchase(state, amount, mi=float(state.monthly_income))
            merch = None
            desc = cat.upper()
            if merchant_agent and merchant_catalog:
                merch = merchant_agent.pick_merchant_for_category(cat, merchant_catalog)
                if merch:
                    desc = merchant_agent.render_description_raw(
                        merch["name"], merch.get("domain", ""),
                    )
            hour = self._sample_hour(cat)
            minute = int(rng.integers(0, 60))
            occurred = make_aware_dt(
                datetime(current.year, current.month, current.day, hour, minute)
            )
            amt_dec = Decimal(str(round(amount, 2)))
            transactions.append({
                "direction": "spend",
                "amount": amt_dec,
                "occurred_at": occurred,
                "category": self._map_category(cat),
                "payment_channel": self._pick_channel(cat),
                "description_raw": desc,
                "merchant_info": merch,
                "subscription_obj": None,
                "spend_category": cat,
            })
            state.balance_proxy -= amt_dec
            day_total += float(amt_dec)

        state.update_liquidity()
        return day_total

    def _day_discretionary(
        self,
        state: UserState,
        context: dict,
        current: date,
        transactions: list,
        merchant_agent,
        merchant_catalog: list,
        month_disc_spent: float,
        disc_cap: float,
        mi: float,
    ) -> float:
        rng = self.rng
        days_since = state.days_since_payday(current)
        payday_window = 1.0 if days_since <= 3 else 0.0
        is_weekend = 1.0 if current.weekday() >= 5 else 0.0
        liq = state.liquidity_numeric()

        for cat in DISCRETIONARY_CATEGORIES:
            if month_disc_spent >= disc_cap:
                break
            p = self._purchase_probability(
                state, cat, is_weekend, payday_window, liq, essential=False,
            )
            # Travel: need liquidity or high income
            if cat == "travel":
                if state.liquidity not in ("comfortable", "stable") and mi < 5000:
                    p *= 0.15

            if rng.random() >= p:
                continue

            amount = self._sample_amount(state, current, cat, days_since, liq)
            amount = self._attenuate_large_purchase(state, amount, mi=mi)
            remaining = max(0.0, disc_cap - month_disc_spent)
            if amount > remaining and remaining < mi * 0.05:
                continue
            amount = min(amount, remaining + mi * 0.1) if remaining < amount else amount

            merch = None
            desc = cat.upper()
            if merchant_agent and merchant_catalog:
                merch = merchant_agent.pick_merchant_for_category(cat, merchant_catalog)
                if merch:
                    desc = merchant_agent.render_description_raw(
                        merch["name"], merch.get("domain", ""),
                    )
            hour = self._sample_hour(cat)
            minute = int(rng.integers(0, 60))
            occurred = make_aware_dt(
                datetime(current.year, current.month, current.day, hour, minute)
            )
            amt_dec = Decimal(str(round(amount, 2)))
            transactions.append({
                "direction": "spend",
                "amount": amt_dec,
                "occurred_at": occurred,
                "category": self._map_category(cat),
                "payment_channel": self._pick_channel(cat),
                "description_raw": desc,
                "merchant_info": merch,
                "subscription_obj": None,
                "spend_category": cat,
            })
            state.balance_proxy -= amt_dec
            month_disc_spent += float(amt_dec)

        state.update_liquidity()
        return month_disc_spent

    def _attenuate_large_purchase(self, state: UserState, amount: float, mi: float) -> float:
        rng = self.rng
        if amount > mi:
            extra = max(0.0, (amount - mi) * 0.5)
            state.balance_proxy -= Decimal(str(round(extra, 2)))
            state.large_purchase_shock_this_month = True
            amount = min(amount, mi * float(rng.uniform(0.85, 1.0)))
        elif amount > 0.6 * mi:
            amount *= float(rng.uniform(0.45, 0.75))
        return max(0.5, amount)

    def _purchase_probability(
        self,
        state: UserState,
        category: str,
        is_weekend: float,
        payday_window: float,
        liq: float,
        *,
        essential: bool,
    ) -> float:
        coeffs = SPEND_LOGISTIC.get(category)
        if not coeffs:
            return 0.02

        z = (coeffs["b0"]
             + coeffs["b_weekend"] * is_weekend
             + coeffs["b_payday"] * payday_window
             + coeffs["b_liq"] * liq
             + coeffs["b_impulse"] * state.impulse
             + coeffs["b_novelty"] * state.novelty_seeking)

        if category == "groceries":
            z += 0.15 * (state.household_size - 1) + 0.1 * state.dependents_count
        if category == "dining":
            z += 0.2 * state.novelty_seeking - 0.3 * liq
        if category == "transport" and state.archetype in ("salary_biweekly", "hourly_weekly"):
            z += 0.3
        if category == "shopping":
            z += 0.15 * state.novelty_seeking * payday_window

        p = logistic(z)
        budget_share = state.category_budgets.get(category, 0.05)
        p *= (0.5 + budget_share * 5)

        if state.liquidity == "overdrafted" and state.impulse < 0.7:
            p *= 0.15
        elif state.liquidity == "overdraft_risk":
            p *= 0.5

        if not essential and state.liquidity in ("tight", "overdraft_risk", "overdrafted"):
            p *= 0.65

        return clip01(p)

    def _sample_amount(
        self,
        state: UserState,
        day: date,
        category: str,
        days_since: int,
        liq: float,
    ) -> float:
        rng = self.rng
        mu, sigma = AMOUNT_LOGNORMAL.get(category, (3.0, 0.6))
        mu += (state.quality_preference - 0.5) * 0.3
        mu -= liq * 0.2
        mu -= state.price_sensitivity * 0.25
        base_amount = sample_lognormal(rng, mu, sigma)
        pm = payday_multiplier(days_since, state.payday_eta, state.payday_tau)
        wm = weekend_multiplier(day, category)
        sm = seasonality_multiplier(day.month, category)
        return max(0.50, base_amount * pm * wm * sm)

    def _sample_hour(self, category: str) -> int:
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
