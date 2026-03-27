"""
Agent 5: Obligation Agent (Section 11)
Generates fixed/semi-fixed recurring obligations outside subscriptions:
rent, utilities, insurance, phone, internet, fees.
Writes to: transactions_transaction
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal

from datagen.agents.base import BaseAgent
from datagen.config import (
    INSURANCE_MONTHLY_P,
    LATE_FEE_P,
    RENT_RATIO_BETA,
    RENT_RATIO_CAP,
    UTILITY_AMOUNT_MEAN,
    UTILITY_AMOUNT_STD,
    resolve_transaction_description,
)
from datagen.distributions import make_aware_dt, sample_beta, sample_truncated_normal, seasonality_multiplier
from datagen.state import UserState


class ObligationAgent(BaseAgent):
    """Generate rent, utilities, insurance, and fee transactions."""

    def run(self, state: UserState, context: dict) -> dict:
        rng = self.rng
        start_date: date = context["start_date"]
        end_date: date = context["end_date"]
        generated_at = context.get("generated_at")

        transactions = []
        monthly_target = max(150.0, float(state.monthly_fixed_expenses or 0))
        rent_share, utility_share, insurance_share, telecom_share = self._monthly_obligation_plan(state)
        monthly_targets = {
            "rent": monthly_target * rent_share,
            "utilities": monthly_target * utility_share,
            "insurance": monthly_target * insurance_share,
            "telecom": monthly_target * telecom_share,
        }

        # Rent (Section 11.4)
        if state.archetype != "student" or rng.random() < 0.3:
            rent_txns = self._generate_rent(state, start_date, end_date, monthly_targets["rent"], generated_at)
            transactions.extend(rent_txns)

        # Utilities
        util_txns = self._generate_utilities(state, start_date, end_date, monthly_targets["utilities"], generated_at)
        transactions.extend(util_txns)

        # Insurance
        ins_txns = self._generate_insurance(state, start_date, end_date, monthly_targets["insurance"], generated_at)
        transactions.extend(ins_txns)

        # Phone/Internet
        phone_txns = self._generate_phone_internet(state, start_date, end_date, monthly_targets["telecom"], generated_at)
        transactions.extend(phone_txns)

        # Update balance
        total = sum(t["amount"] for t in transactions)
        state.balance_proxy -= total
        state.update_liquidity()

        return {"obligation_transactions": transactions}

    def _monthly_obligation_plan(self, state: UserState) -> tuple[float, float, float, float]:
        if state.housing_independence_state == "dependent":
            return (0.0, 0.36, 0.22, 0.42)
        if state.housing_independence_state == "shared":
            return (0.42, 0.20, 0.14, 0.24)
        if state.housing_independence_state == "homeowner":
            return (0.60, 0.18, 0.12, 0.10)
        return (0.56, 0.18, 0.11, 0.15)

    def _generate_rent(self, state: UserState, start: date, end: date, monthly_target: float, generated_at) -> list[dict]:
        rng = self.rng
        if state.housing_independence_state == "dependent" and rng.random() < 0.92:
            return []
        ratio = sample_beta(rng, *RENT_RATIO_BETA)
        ratio = max(RENT_RATIO_CAP[0], min(RENT_RATIO_CAP[1], ratio))
        baseline = float(state.monthly_income) * ratio
        if state.housing_independence_state == "shared":
            baseline *= 0.55
        blended = max(100.0, baseline * 0.15 + monthly_target * 0.85)
        blended = min(blended, max(100.0, monthly_target * 1.08))
        rent_amount = Decimal(str(round(blended, 2)))

        txns = []
        current = date(start.year, start.month, 1)

        landlord = rng.choice([
            "RENT PAYMENT", "PROPERTY MGMT", "APT RENT",
            "HOUSING PAYMENT", "LEASE PAYMENT",
        ])

        while current <= end:
            # Rent due 1st of month, paid on 1st-3rd
            pay_day = min(current.replace(day=1) + timedelta(days=int(rng.integers(0, 3))), end)
            if pay_day >= start:
                noise = Decimal(str(round(float(rng.normal(0, 5)), 2)))
                amount = max(Decimal("100"), rent_amount + noise)
                txns.append(self._make_txn(pay_day, amount, landlord, "bills", state, generated_at))

                # Late fee
                late_p = rng.uniform(*LATE_FEE_P)
                if state.liquidity in ("tight", "overdraft_risk", "overdrafted"):
                    late_p *= 3
                if rng.random() < late_p:
                        fee_date = pay_day + timedelta(days=int(rng.integers(5, 15)))
                        if fee_date <= end:
                            fee = Decimal(str(round(float(rng.uniform(25, 75)), 2)))
                            txns.append(self._make_txn(fee_date, fee, "LATE FEE - RENT", "bills", state, generated_at))

            if current.month == 12:
                current = date(current.year + 1, 1, 1)
            else:
                current = date(current.year, current.month + 1, 1)

        return txns

    def _generate_utilities(self, state: UserState, start: date, end: date, monthly_target: float, generated_at) -> list[dict]:
        rng = self.rng
        mean = max(45.0, monthly_target)
        std = max(8.0, mean * 0.18)
        txns = []

        utility_names = [
            ("ELECTRIC BILL", "utilities"),
            ("WATER BILL", "utilities"),
            ("GAS BILL", "utilities"),
        ]
        # Each user has 1-3 utility bills
        n_utils = int(rng.integers(1, 4))
        chosen_utils = [utility_names[i % len(utility_names)] for i in range(n_utils)]

        current = date(start.year, start.month, 1)
        while current <= end:
            for util_name, cat in chosen_utils:
                pay_day = current.replace(day=min(28, int(rng.integers(10, 25))))
                if start <= pay_day <= end:
                    seasonal = seasonality_multiplier(pay_day.month, "utilities")
                    amount = sample_truncated_normal(rng, mean / n_utils, std / max(1, n_utils), lo=20.0, hi=500.0)
                    amount *= seasonal
                    txns.append(self._make_txn(
                        pay_day, Decimal(str(round(amount, 2))),
                        util_name, cat, state, generated_at
                    ))

            if current.month == 12:
                current = date(current.year + 1, 1, 1)
            else:
                current = date(current.year, current.month + 1, 1)

        return txns

    def _generate_insurance(self, state: UserState, start: date, end: date, monthly_target: float, generated_at) -> list[dict]:
        rng = self.rng
        if state.archetype == "student" and rng.random() < 0.6:
            return []

        annual_premium = max(180.0, monthly_target * 12.0 * float(rng.uniform(0.82, 0.98)))
        is_monthly = rng.random() < max(0.85, INSURANCE_MONTHLY_P)
        txns = []

        if is_monthly:
            monthly_amt = Decimal(str(round(annual_premium / 12, 2)))
            current = date(start.year, start.month, 1)
            while current <= end:
                pay_day = current.replace(day=min(28, int(rng.integers(1, 15))))
                if start <= pay_day <= end:
                    txns.append(self._make_txn(
                        pay_day, monthly_amt,
                        "INSURANCE PREMIUM", "bills", state, generated_at
                    ))
                if current.month == 12:
                    current = date(current.year + 1, 1, 1)
                else:
                    current = date(current.year, current.month + 1, 1)
        else:
            # Semi-annual only; full annual lump sums over-short windows read as synthetic spikes.
            for year in range(start.year, end.year + 1):
                for month in [1, 7]:
                    pay_date = date(year, month, int(rng.integers(1, 15)))
                    if start <= pay_date <= end:
                        amt = Decimal(str(round(annual_premium / 2, 2)))
                        txns.append(self._make_txn(
                            pay_date, amt,
                            "INSURANCE PREMIUM ANNUAL", "bills", state, generated_at
                        ))

        return txns

    def _generate_phone_internet(self, state: UserState, start: date, end: date, monthly_target: float, generated_at) -> list[dict]:
        rng = self.rng
        txns = []
        phone_share = float(rng.uniform(0.45, 0.62))
        phone_cost = Decimal(str(round(max(20.0, monthly_target * phone_share), 2)))
        internet_cost = Decimal(str(round(max(30.0, monthly_target - float(phone_cost)), 2)))

        current = date(start.year, start.month, 1)
        while current <= end:
            # Phone bill
            ph_day = current.replace(day=min(28, int(rng.integers(5, 20))))
            if start <= ph_day <= end:
                noise = Decimal(str(round(float(rng.normal(0, 3)), 2)))
                txns.append(self._make_txn(
                    ph_day, max(Decimal("20"), phone_cost + noise),
                    rng.choice(["T-MOBILE BILL", "AT&T WIRELESS", "VERIZON WIRELESS"]),
                    "bills", state, generated_at
                ))

            # Internet bill
            inet_day = current.replace(day=min(28, int(rng.integers(10, 25))))
            if start <= inet_day <= end:
                noise = Decimal(str(round(float(rng.normal(0, 2)), 2)))
                txns.append(self._make_txn(
                    inet_day, max(Decimal("30"), internet_cost + noise),
                    rng.choice(["XFINITY INTERNET", "SPECTRUM", "ATT INTERNET"]),
                    "bills", state, generated_at
                ))

            if current.month == 12:
                current = date(current.year + 1, 1, 1)
            else:
                current = date(current.year, current.month + 1, 1)

        return txns

    def _make_txn(self, txn_date: date, amount: Decimal, description: str,
                  category: str, state: UserState, generated_at=None) -> dict:
        rng = self.rng
        hour = int(rng.integers(6, 12))
        minute = int(rng.integers(0, 60))
        occurred = make_aware_dt(
            datetime(txn_date.year, txn_date.month, txn_date.day, hour, minute)
        )
        if generated_at is not None and occurred > generated_at:
            occurred = generated_at

        return {
            "direction": "spend",
            "amount": abs(amount),
            "occurred_at": occurred,
            "category": (resolve_transaction_description(description, direction="spend", fallback_category=category) or {}).get("category", category),
            "payment_channel": rng.choice(["bank", "online"]),
            "description_raw": description.upper(),
            "merchant_info": (resolve_transaction_description(description, direction="spend", fallback_category=category) or {}).get("merchant_info"),
            "merchant_obj": None,
            "subscription_obj": None,
        }
