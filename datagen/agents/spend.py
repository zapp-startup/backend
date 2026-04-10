"""
Agent 6: Spend Agent (Section 12)
Intent-first spending: user state -> intent -> category -> merchant family -> merchant -> amount.
Essential then discretionary passes per calendar month; logistic purchase probability;
LogNormal amounts with family-specific priors after merchant selection.
Writes to: transactions_transaction
"""

from __future__ import annotations

from calendar import monthrange
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal

from datagen.agents.base import BaseAgent
from datagen.config import (
    AMOUNT_LOGNORMAL,
    AMOUNT_PRIORS_BY_FAMILY,
    SPEND_LOGISTIC,
    TRANSACTION_VARIABLE_ESSENTIAL_SHARE_BY_HOUSING,
    TRANSACTION_VARIABLE_FLOOR_BY_HOUSING,
    spend_to_txn_category,
)
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


@dataclass
class SpendRoutine:
    """Per-user habit state for repeated patterns (pharmacy cadence, fuel spacing, etc.)."""

    pharmacy_days_since_refill: int = field(default_factory=lambda: 14)
    fuel_days_since: int = 0
    grocery_trip_index: int = 0
    grocery_days_since: int = field(default_factory=lambda: 3)
    commuter: bool = True


class SpendAgent(BaseAgent):
    """Generate spending: intent-first essential then discretionary passes per month."""

    def run(self, state: UserState, context: dict) -> dict:
        rng = self.rng
        start_date: date = context["start_date"]
        end_date: date = context["end_date"]
        merchant_agent = context.get("merchant_agent")
        merchant_catalog = context.get("merchant_catalog", [])
        generated_at = context.get("generated_at")

        transactions: list[dict] = []
        mi = float(state.monthly_income or 1)
        routine = SpendRoutine()
        routine.commuter = bool(rng.random() < 0.72)
        routine.pharmacy_days_since_refill = int(rng.integers(10, 22))

        for _ym, seg_start, seg_end in _iter_month_segments(start_date, end_date):
            state.large_purchase_shock_this_month = False
            sub_m = _monthly_subscription_cost_from_context(context)
            fixed_m = float(state.monthly_fixed_expenses)
            income_m = mi
            variable_budget = self._monthly_variable_budget(
                state,
                monthly_income=income_m,
                fixed_monthly=fixed_m,
                subscription_monthly=sub_m,
            )
            essential_cap = variable_budget * self._essential_budget_share(state, routine)
            essential_month = 0.0

            d = seg_start
            while d <= seg_end:
                essential_month += self._day_essential(
                    state,
                    context,
                    d,
                    transactions,
                    merchant_agent,
                    merchant_catalog,
                    routine,
                    mi,
                    essential_month,
                    essential_cap,
                    generated_at,
                )
                d += timedelta(days=1)

            reserve = min(
                income_m * (0.04 + 0.05 * state.income_stability_numeric()),
                variable_budget * 0.18,
            )
            remaining_variable = max(0.0, variable_budget - essential_month)
            disc_cap = max(0.0, remaining_variable - reserve)
            if state.budget_adherence >= 0.65:
                disc_cap *= 0.80
            elif state.budget_adherence >= 0.45:
                disc_cap *= 0.86
            else:
                disc_cap *= 0.93
            month_disc_spent = 0.0

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
                    routine,
                    generated_at,
                )
                d += timedelta(days=1)

        return {"spend_transactions": transactions}

    def _disc_compression(self, state: UserState, current: date) -> float:
        """Late-month discretionary compression for budget-tight users."""
        if current.day < 25:
            return 1.0
        if state.liquidity not in ("tight", "overdraft_risk", "overdrafted"):
            return 1.0
        if state.budget_adherence < 0.45:
            return 1.0
        return 0.52

    def _monthly_variable_budget(
        self,
        state: UserState,
        *,
        monthly_income: float,
        fixed_monthly: float,
        subscription_monthly: float,
    ) -> float:
        target_ratio = float(getattr(state, "transaction_spend_target_ratio", 0.56) or 0.56)
        target_ratio = max(0.42, min(0.76, target_ratio))
        base_budget = monthly_income * target_ratio - fixed_monthly - subscription_monthly

        floor_ratio = TRANSACTION_VARIABLE_FLOOR_BY_HOUSING.get(
            state.housing_independence_state,
            TRANSACTION_VARIABLE_FLOOR_BY_HOUSING["independent"],
        )
        floor_ratio += 0.015 * max(0, state.household_size - 1)
        floor_ratio += 0.01 * state.dependents_count
        if state.archetype == "student":
            floor_ratio -= 0.015
        elif state.archetype == "retired":
            floor_ratio += 0.01
        floor_ratio = max(0.10, min(0.30, floor_ratio))

        debt_flex = state.debt_capacity_monthly() * 0.12
        return max(monthly_income * floor_ratio, base_budget + debt_flex)

    def _essential_budget_share(self, state: UserState, routine: SpendRoutine) -> float:
        share = TRANSACTION_VARIABLE_ESSENTIAL_SHARE_BY_HOUSING.get(
            state.housing_independence_state,
            TRANSACTION_VARIABLE_ESSENTIAL_SHARE_BY_HOUSING["independent"],
        )
        share += 0.025 * max(0, state.household_size - 1)
        share += 0.015 * state.dependents_count
        share += 0.05 * max(0.0, state.price_sensitivity - 0.5)
        share += 0.03 * max(0.0, state.household_pressure - 0.5)
        share -= 0.04 * max(0.0, state.novelty_seeking - 0.5)
        share -= 0.05 * max(0.0, state.luxury_affinity - 0.5)
        if not routine.commuter:
            share -= 0.03
        if state.archetype == "student":
            share -= 0.02
        return max(0.48, min(0.82, share))

    def _intent_for_essential_transport(self, state: UserState, current: date, routine: SpendRoutine) -> str:
        rng = self.rng
        is_weekday = current.weekday() < 5
        routine.fuel_days_since += 1
        p_commute = 0.62 if (routine.commuter and is_weekday) else 0.28
        p_commute += 0.12 if is_weekday else -0.08
        p_commute = clip01(p_commute)
        if rng.random() < p_commute:
            return "commute"
        if routine.fuel_days_since >= 5 or rng.random() < 0.22:
            routine.fuel_days_since = 0
            return "fuel_stop"
        return "commute"

    def _emit_transaction(
        self,
        state: UserState,
        current: date,
        *,
        spend_category: str,
        intent: str,
        merchant_family: str,
        transactions: list,
        merchant_agent,
        merchant_catalog: list,
        days_since: int,
        liq: float,
        mi: float,
        anomaly_mode: bool = False,
        budget_remaining: float | None = None,
        generated_at=None,
    ) -> float:
        rng = self.rng
        if not merchant_agent or not merchant_catalog:
            return 0.0
        merch = merchant_agent.pick_merchant_for_family(
            merchant_family, spend_category, merchant_catalog, anomaly_mode=anomaly_mode,
        )
        if not merch:
            return 0.0
        fam = merch.get("merchant_family", merchant_family)
        amount = self._sample_amount_for_family(
            state, current, spend_category, fam, days_since, liq, mi,
        )
        amount = self._attenuate_large_purchase(state, amount, mi=mi, merchant_family=fam)
        if budget_remaining is not None:
            budget_remaining = max(0.0, float(budget_remaining))
            if budget_remaining < 0.5:
                return 0.0
            amount = min(amount, budget_remaining)
        desc = merchant_agent.render_description_raw(merch["name"], merch.get("domain", ""))
        hour = self._sample_hour(spend_category)
        minute = int(rng.integers(0, 60))
        occurred = make_aware_dt(
            datetime(current.year, current.month, current.day, hour, minute)
        )
        if generated_at is not None and occurred > generated_at:
            occurred = generated_at
        amt_dec = Decimal(str(round(amount, 2)))
        transactions.append({
            "direction": "spend",
            "amount": amt_dec,
            "occurred_at": occurred,
            "category": self._map_category(spend_category),
            "payment_channel": self._pick_channel(spend_category),
            "description_raw": desc,
            "merchant_info": merch,
            "subscription_obj": None,
            "spend_category": spend_category,
            "spend_intent": intent,
            "merchant_family": fam,
        })
        state.balance_proxy -= amt_dec
        return float(amt_dec)

    def _day_essential(
        self,
        state: UserState,
        context: dict,
        current: date,
        transactions: list,
        merchant_agent,
        merchant_catalog: list,
        routine: SpendRoutine,
        mi: float,
        month_essential_spent: float,
        essential_cap: float,
        generated_at=None,
    ) -> float:
        rng = self.rng
        day_total = 0.0
        days_since = state.days_since_payday(current)
        payday_window = 1.0 if days_since <= 3 else 0.0
        is_weekend = 1.0 if current.weekday() >= 5 else 0.0
        liq = state.liquidity_numeric()

        for cat in ESSENTIAL_CATEGORIES:
            if cat == "groceries":
                intent = "grocery_run"
                merchant_family = "grocery_retail"
            else:
                intent = self._intent_for_essential_transport(state, current, routine)
                merchant_family = "rideshare" if intent == "commute" else "fuel"

            p = self._purchase_probability(
                state, cat, is_weekend, payday_window, liq, essential=True,
            )
            if essential_cap > 0:
                essential_pressure = clip01((month_essential_spent + day_total) / essential_cap)
                if cat == "groceries":
                    p *= max(0.08, 1.0 - 0.88 * essential_pressure)
                else:
                    p *= max(0.14, 1.0 - 0.72 * essential_pressure)
            if cat == "groceries":
                routine.grocery_days_since += 1
                if routine.grocery_days_since <= 1:
                    p *= 0.08
                elif routine.grocery_days_since == 2:
                    p *= 0.35
                elif routine.grocery_days_since == 3:
                    p *= 0.72
                p *= 1.05 if is_weekend > 0 else 0.78
                routine.grocery_trip_index += 1
            if rng.random() >= p:
                continue

            day_total += self._emit_transaction(
                state,
                current,
                spend_category=cat,
                intent=intent,
                merchant_family=merchant_family,
                transactions=transactions,
                merchant_agent=merchant_agent,
                merchant_catalog=merchant_catalog,
                days_since=days_since,
                liq=liq,
                mi=mi,
                budget_remaining=None if essential_cap <= 0 else max(0.0, essential_cap - month_essential_spent - day_total),
                generated_at=generated_at,
            )
            if cat == "groceries":
                routine.grocery_days_since = 0

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
        routine: SpendRoutine,
        generated_at=None,
    ) -> float:
        rng = self.rng
        if state.luxury_cooldown_days > 0:
            state.luxury_cooldown_days = max(0, state.luxury_cooldown_days - 1)
        days_since = state.days_since_payday(current)
        payday_window = 1.0 if days_since <= 3 else 0.0
        is_weekend = 1.0 if current.weekday() >= 5 else 0.0
        liq = state.liquidity_numeric()
        disc_mult = self._disc_compression(state, current)

        for cat in DISCRETIONARY_CATEGORIES:
            if month_disc_spent >= disc_cap:
                break

            intent, merchant_family = self._discretionary_intent(
                cat, state, current, routine, payday_window=payday_window, liq=liq, mi=mi,
            )

            p = self._purchase_probability(
                state, cat, is_weekend, payday_window, liq, essential=False,
            )
            p *= disc_mult
            if cat == "health":
                routine.pharmacy_days_since_refill += 1
                if routine.pharmacy_days_since_refill > 21:
                    p *= 1.35
                if routine.pharmacy_days_since_refill > 35:
                    p *= 1.2
            if cat == "shopping" and payday_window > 0:
                p *= 1.18

            if cat == "travel":
                if state.liquidity not in ("comfortable", "stable") and mi < 5000:
                    p *= 0.15

            if rng.random() >= p:
                continue

            if not merchant_agent or not merchant_catalog:
                continue
            merch = self._pick_discretionary_merchant(
                state,
                context,
                spend_category=cat,
                intent=intent,
                merchant_family=merchant_family,
                merchant_agent=merchant_agent,
                merchant_catalog=merchant_catalog,
            )
            if not merch:
                continue
            fam = merch.get("merchant_family", merchant_family)
            amount = self._sample_amount_for_family(
                state, current, cat, fam, days_since, liq, mi,
            )
            amount = self._attenuate_large_purchase(state, amount, mi=mi, merchant_family=fam)
            remaining = max(0.0, disc_cap - month_disc_spent)
            if amount > remaining and remaining < mi * 0.05:
                continue
            amount = min(amount, remaining + mi * 0.1) if remaining < amount else amount

            desc = merchant_agent.render_description_raw(
                merch["name"], merch.get("domain", ""),
            )
            hour = self._sample_hour(cat)
            minute = int(rng.integers(0, 60))
            occurred = make_aware_dt(
                datetime(current.year, current.month, current.day, hour, minute)
            )
            if generated_at is not None and occurred > generated_at:
                occurred = generated_at
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
                "spend_intent": intent,
                "merchant_family": fam,
            })
            if cat == "health":
                routine.pharmacy_days_since_refill = 0
            if fam == "luxury_retail":
                state.luxury_cooldown_days = int(rng.integers(9, 22))
                if float(amt_dec) > mi * 0.12:
                    state.large_purchase_shock_this_month = True
            state.balance_proxy -= amt_dec
            month_disc_spent += float(amt_dec)

        state.update_liquidity()
        return month_disc_spent

    def _pick_discretionary_merchant(
        self,
        state: UserState,
        context: dict,
        *,
        spend_category: str,
        intent: str,
        merchant_family: str,
        merchant_agent,
        merchant_catalog: list,
    ) -> dict | None:
        if intent == "meal_delivery" and merchant_family == "delivery_membership":
            preferred = self._pick_delivery_usage_merchant(context, merchant_catalog)
            if preferred is not None:
                return preferred
        return merchant_agent.pick_merchant_for_family(
            merchant_family, spend_category, merchant_catalog, anomaly_mode=False,
        )

    def _pick_delivery_usage_merchant(self, context: dict, merchant_catalog: list) -> dict | None:
        rng = self.rng
        active_delivery_subs = [
            s for s in context.get("subscriptions", [])
            if s.get("status") == "active"
            and (s.get("merchant_info") or {}).get("merchant_family") == "delivery_membership"
        ]
        catalog_by_name = {m.get("name"): m for m in merchant_catalog}

        if active_delivery_subs and rng.random() < 0.72:
            preferred_names = [
                (s.get("merchant_info") or {}).get("name")
                for s in active_delivery_subs
                if (s.get("merchant_info") or {}).get("name") in catalog_by_name
            ]
            if preferred_names:
                return dict(catalog_by_name[str(rng.choice(preferred_names))])

        candidates = [
            m for m in merchant_catalog
            if m.get("merchant_family") == "delivery_membership"
        ]
        if not candidates:
            return None
        return dict(rng.choice(candidates))

    def _shopping_intent(
        self,
        state: UserState,
        *,
        payday_window: float,
        liq: float,
        mi: float,
    ) -> tuple[str, str]:
        rng = self.rng
        luxury_signal = (
            0.52 * state.luxury_affinity
            + 0.20 * state.quality_preference
            + 0.12 * state.novelty_seeking
            + 0.10 * max(0.0, 1.0 - state.price_sensitivity)
        )
        if mi >= 8000 and state.liquidity in ("comfortable", "stable"):
            luxury_signal += 0.12
        elif mi < 3500 or state.liquidity in ("tight", "overdraft_risk", "overdrafted"):
            luxury_signal -= 0.18
        luxury_signal += 0.05 * payday_window
        if state.luxury_cooldown_days > 0:
            luxury_signal -= 0.25
        if state.large_purchase_shock_this_month:
            luxury_signal -= 0.10

        aspirational = 0.0
        if mi < 5000 and state.credit_stress > 0.5:
            aspirational = 0.03 + 0.04 * state.luxury_affinity

        p_luxury = min(0.24, max(0.0, luxury_signal * 0.16 + aspirational + float(rng.normal(0, 0.01))))
        if rng.random() < p_luxury:
            return "luxury_splurge", "luxury_retail"
        if rng.random() < 0.88:
            return "online_order", "ecommerce"
        return "retail_trip", "retail_big_box"

    def _discretionary_intent(
        self,
        cat: str,
        state: UserState,
        current: date,
        routine: SpendRoutine | None,
        *,
        payday_window: float = 0.0,
        liq: float = 0.0,
        mi: float = 0.0,
    ) -> tuple[str, str]:
        rng = self.rng
        if cat == "dining":
            if rng.random() < 0.42:
                return "meal_delivery", "delivery_membership"
            return "quick_meal", "food_quick"
        if cat == "shopping":
            return self._shopping_intent(state, payday_window=payday_window, liq=liq, mi=mi)
        if cat == "health":
            return "pharmacy_refill", "pharmacy"
        if cat == "entertainment":
            return "night_out", "entertainment_out"
        if cat == "travel":
            return "travel", "travel"
        return "misc", "ecommerce"

    def _attenuate_large_purchase(
        self,
        state: UserState,
        amount: float,
        mi: float,
        *,
        merchant_family: str,
    ) -> float:
        rng = self.rng
        large_ok = merchant_family in ("ecommerce", "travel", "retail_big_box", "grocery_retail", "luxury_retail")
        if amount > mi and not large_ok:
            extra = max(0.0, (amount - mi) * 0.5)
            state.balance_proxy -= Decimal(str(round(extra, 2)))
            state.large_purchase_shock_this_month = True
            amount = min(amount, mi * float(rng.uniform(0.85, 1.0)))
        elif amount > 0.6 * mi and not large_ok:
            amount *= float(rng.uniform(0.45, 0.75))
        elif amount > 0.45 * mi and not large_ok:
            amount *= float(rng.uniform(0.65, 0.9))
        return max(0.5, amount)

    def _sample_amount_for_family(
        self,
        state: UserState,
        day: date,
        spend_category: str,
        merchant_family: str,
        days_since: int,
        liq: float,
        mi: float,
    ) -> float:
        rng = self.rng
        mu, sigma = AMOUNT_PRIORS_BY_FAMILY.get(
            merchant_family,
            AMOUNT_LOGNORMAL.get(spend_category, (3.0, 0.6)),
        )
        mu += (state.quality_preference - 0.5) * 0.3
        mu -= liq * 0.2
        mu -= state.price_sensitivity * 0.25
        if merchant_family == "luxury_retail":
            mu += 0.35 * state.luxury_affinity
            mu += 0.18 * state.quality_preference
        if merchant_family == "grocery_retail":
            mu += 0.14 * max(0, state.household_size - 1)
            mu += 0.05 * state.dependents_count
        base_amount = sample_lognormal(rng, mu, sigma)
        if merchant_family == "grocery_retail":
            base_amount *= 0.78
        pm = payday_multiplier(days_since, state.payday_eta, state.payday_tau)
        wm = weekend_multiplier(day, spend_category)
        sm = seasonality_multiplier(day.month, spend_category)
        return max(0.50, base_amount * pm * wm * sm)

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
        target_ratio = float(getattr(state, "transaction_spend_target_ratio", 0.56) or 0.56)
        if essential:
            p *= max(0.82, min(1.10, 0.95 + (target_ratio - 0.56) * 0.9))
        else:
            p *= max(0.70, min(1.18, 0.90 + (target_ratio - 0.56) * 1.4))

        if state.liquidity == "overdrafted" and state.impulse < 0.7:
            p *= 0.15
        elif state.liquidity == "overdraft_risk":
            p *= 0.5

        if not essential and state.liquidity in ("tight", "overdraft_risk", "overdrafted"):
            p *= 0.65

        return clip01(p)

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
        return spend_to_txn_category(spend_cat)

    def _pick_channel(self, category: str) -> str:
        rng = self.rng
        if category in ("groceries", "dining"):
            return str(rng.choice(["card", "card", "card", "cash"]))
        if category in ("shopping", "entertainment", "travel"):
            return str(rng.choice(["card", "online", "online"]))
        return str(rng.choice(["card", "online", "bank"]))
