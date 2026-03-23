"""
Agent 4: Subscription Agent (Section 10)
Generates subscriptions, links them to merchants, creates recurring charges,
and simulates full lifecycle: trial, paid, price increase, failed payment,
cancellation, reactivation.
Writes to: subscriptions_subscription, transactions_transaction
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal

from datagen.agents.base import BaseAgent
from datagen.config import (
    ANNUAL_BILLING_PROBABILITY,
    CANCEL_BASE_GAMMA,
    CANCEL_CHURN_GAMMA,
    CANCEL_OVERLOAD_GAMMA,
    CANCEL_STRETCH_GAMMA,
    CANCEL_TIGHT_GAMMA,
    FAILED_CHARGE_PROBABILITY,
    PRICE_INCREASE_YEARLY,
    REACTIVATION_PROBABILITY,
    SUBSCRIPTION_CATEGORIES,
    SUBSCRIPTION_COUNT_LAMBDA,
    SUBSCRIPTION_MERCHANT_PRICE_TIERS,
    SUBSCRIPTION_PRICE_LOGNORMAL,
    TRIAL_PROBABILITY,
)
from datagen.distributions import make_aware_dt, sample_lognormal_decimal, sample_poisson
from datagen.state import UserState


def _sample_tier_price(rng, tiers: list[tuple[str, float]]) -> Decimal:
    r = rng.random()
    acc = 0.0
    chosen = tiers[0][0]
    for price_s, w in tiers:
        acc += w
        if r <= acc:
            chosen = price_s
            break
    base = Decimal(chosen)
    noise = Decimal(str(round(float(rng.normal(0, 0.22)), 2)))
    return max(Decimal("0.99"), base + noise)


def _monthly_subscription_cost(sub: dict) -> float:
    price = float(sub.get("price", 0) or 0)
    billing_cycle = sub.get("billing_cycle", "monthly")
    if billing_cycle == "yearly":
        return price / 12.0
    if billing_cycle == "weekly":
        return price * 52.0 / 12.0
    return price


class SubscriptionAgent(BaseAgent):
    """Generate subscriptions and their recurring charge transactions."""

    def _price_for_merchant(self, merch: dict, sub_cat: str) -> Decimal:
        rng = self.rng
        name = merch.get("name", "")
        tiers = SUBSCRIPTION_MERCHANT_PRICE_TIERS.get(name)
        if tiers:
            return _sample_tier_price(rng, tiers)
        mu, sig = SUBSCRIPTION_PRICE_LOGNORMAL.get(
            sub_cat, SUBSCRIPTION_PRICE_LOGNORMAL["streaming"]
        )
        base_price = sample_lognormal_decimal(rng, mu, sig)
        return max(Decimal("1.99"), min(Decimal("99.99"), base_price))

    def _yearly_billing_draw(self, merch: dict, sub_cat: str) -> bool:
        rng = self.rng
        mode = merch.get("yearly_billing_mode")
        if mode == "forbidden":
            return False
        if mode == "rare":
            return rng.random() < 0.02
        if mode == "low":
            return rng.random() < 0.08
        if mode == "normal":
            p = ANNUAL_BILLING_PROBABILITY.get(sub_cat, ANNUAL_BILLING_PROBABILITY["default"])
            return rng.random() < p
        p = min(ANNUAL_BILLING_PROBABILITY.get(sub_cat, ANNUAL_BILLING_PROBABILITY["default"]), 0.12)
        return rng.random() < p

    def run(self, state: UserState, context: dict) -> dict:
        rng = self.rng
        start_date: date = context["start_date"]
        end_date: date = context["end_date"]
        merchant_catalog = context.get("merchant_catalog", [])
        merchant_agent = context.get("merchant_agent")

        eng = state.subscription_engagement
        lam_lo, lam_hi = SUBSCRIPTION_COUNT_LAMBDA[eng]
        lam = rng.uniform(lam_lo, lam_hi)
        n_subs = max(1, sample_poisson(rng, lam))

        sub_merchants = self._pick_subscription_merchants(n_subs, merchant_catalog)

        subscriptions = []
        sub_transactions = []

        for i, merch in enumerate(sub_merchants):
            sub_cat = str(rng.choice(SUBSCRIPTION_CATEGORIES))
            base_price = self._price_for_merchant(merch, sub_cat)

            is_annual = self._yearly_billing_draw(merch, sub_cat)
            billing_cycle = "yearly" if is_annual else "monthly"

            if is_annual:
                monthly_equiv = float(base_price)
                annual = monthly_equiv * 12.0 * float(rng.uniform(0.96, 1.04))
                base_price = Decimal(str(round(annual, 2)))

            # Trial probability
            has_trial = rng.random() < rng.uniform(*TRIAL_PROBABILITY)

            # Started somewhere in the window
            days_range = (end_date - start_date).days
            started_on = start_date + timedelta(days=int(rng.integers(0, max(1, days_range - 60))))
            sub_key = f"{merch['name']}::{started_on.isoformat()}"

            # Simulate lifecycle
            sub_data, charges = self._simulate_lifecycle(
                state, started_on, end_date, base_price,
                billing_cycle, has_trial, merch, merchant_agent, sub_key,
            )
            sub_data["merchant_info"] = merch
            sub_data["_sub_key"] = sub_key
            sub_data["plan_name"] = str(rng.choice(["Basic", "Standard", "Premium", "Plus", "Student", None]))

            subscriptions.append(sub_data)
            for c in charges:
                c["_sub_key"] = sub_key
            sub_transactions.extend(charges)

            active_cost = sum(
                _monthly_subscription_cost(s)
                for s in subscriptions
                if s.get("status") == "active"
            )
            state.update_subscription_burden(active_cost)

        # Update balance
        total_sub_cost = sum(t["amount"] for t in sub_transactions)
        state.balance_proxy -= total_sub_cost
        state.update_liquidity()

        active_monthly = sum(
            _monthly_subscription_cost(s)
            for s in subscriptions
            if s.get("status") == "active"
        )
        state.update_subscription_burden(active_monthly)

        return {
            "subscriptions": subscriptions,
            "subscription_transactions": sub_transactions,
        }

    def _simulate_lifecycle(self, state: UserState, started: date, end: date,
                            price: Decimal, billing_cycle: str, has_trial: bool,
                            merch: dict, merchant_agent, sub_key: str) -> tuple[dict, list[dict]]:
        rng = self.rng
        charges: list[dict] = []
        current_price = price
        status = "active"
        cancelled_on = None
        reactivation_count = 0
        usage_frequency = int(rng.integers(0, 8))

        period_days = 365 if billing_cycle == "yearly" else 30
        current = started

        # Trial period
        if has_trial:
            trial_end = current + timedelta(days=int(rng.choice([7, 14, 30])))
            current = trial_end

        while current <= end:
            if status == "canceled":
                # Check for reactivation
                reactivate_p = rng.uniform(*REACTIVATION_PROBABILITY)
                if rng.random() < reactivate_p / 6:  # per-month probability
                    status = "active"
                    reactivation_count += 1
                    current += timedelta(days=period_days)
                    continue
                else:
                    current += timedelta(days=period_days)
                    continue

            # Failed charge (Section 10.5)
            fail_p = rng.uniform(*FAILED_CHARGE_PROBABILITY)
            if rng.random() < fail_p:
                retry_lag = int(rng.integers(1, 4))
                retry_date = current + timedelta(days=retry_lag)
                if retry_date <= end:
                    charges.append(self._make_charge(
                        retry_date, current_price, merch, merchant_agent, state, sub_key,
                    ))
                current += timedelta(days=period_days)
                continue

            # Normal charge
            charges.append(self._make_charge(
                current, current_price, merch, merchant_agent, state, sub_key,
            ))

            # Price increase check (yearly)
            increase_p = rng.uniform(*PRICE_INCREASE_YEARLY) / (12 if billing_cycle == "monthly" else 1)
            if rng.random() < increase_p:
                bump = Decimal(str(round(float(current_price) * float(rng.uniform(0.05, 0.15)), 2)))
                current_price += bump

            # Cancellation check (no valuation leakage — burden + usage + liquidity only)
            cancel_p = CANCEL_BASE_GAMMA
            if state.subscription_engagement == "churn_prone":
                cancel_p += CANCEL_CHURN_GAMMA
            if state.liquidity in ("tight", "overdraft_risk", "overdrafted"):
                cancel_p += CANCEL_TIGHT_GAMMA
            if state.subscription_burden_state == "stretched":
                cancel_p += CANCEL_STRETCH_GAMMA
            if state.subscription_burden_state == "overloaded":
                cancel_p += CANCEL_OVERLOAD_GAMMA
            if usage_frequency < 2:
                cancel_p += 0.02

            if rng.random() < cancel_p:
                status = "canceled"
                cancelled_on = current + timedelta(days=int(rng.integers(0, period_days)))
                if cancelled_on > end:
                    cancelled_on = end

            current += timedelta(days=period_days)

        # Determine renewal date for active subs
        renewal_date = None
        if status == "active":
            next_charge = current
            if next_charge > end:
                renewal_date = next_charge
            else:
                renewal_date = end + timedelta(days=int(rng.integers(1, period_days)))

        return {
            "status": status,
            "billing_cycle": billing_cycle,
            "price": current_price,
            "started_on": started,
            "renewal_date": renewal_date,
            "cancelled_on": cancelled_on if status == "canceled" else None,
            "reactivation_count": reactivation_count,
            "usage_frequency": usage_frequency,
        }, charges

    def _make_charge(self, charge_date: date, price: Decimal, merch: dict,
                     merchant_agent, state: UserState, sub_key: str) -> dict:
        rng = self.rng
        hour = int(rng.integers(0, 6))
        minute = int(rng.integers(0, 60))
        occurred = make_aware_dt(
            datetime(charge_date.year, charge_date.month, charge_date.day, hour, minute)
        )

        desc = merch["name"].upper()
        if merchant_agent:
            desc = merchant_agent.render_description_raw(
                merch["name"], merch.get("domain", "")
            )

        return {
            "direction": "spend",
            "amount": price,
            "occurred_at": occurred,
            "category": "subscriptions",
            "payment_channel": "online",
            "description_raw": desc,
            "merchant_info": merch,
            "subscription_idx": None,  # linked later in pipeline
            "_sub_key": sub_key,
        }

    def _pick_subscription_merchants(self, n: int, catalog: list[dict]) -> list[dict]:
        rng = self.rng
        sub_cats = ["streaming", "software", "fitness", "education", "food", "utilities"]
        eligible = [
            m for m in catalog
            if m.get("eligibility", "not_subscribable") != "not_subscribable"
        ]
        candidates = [m for m in eligible if m["category"] in sub_cats]
        if not candidates:
            candidates = eligible
        if not candidates:
            return []
        n = min(n, len(candidates))
        indices = rng.choice(len(candidates), size=n, replace=False)
        return [candidates[i] for i in indices]
