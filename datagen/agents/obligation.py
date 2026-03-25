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
        merchant_agent = context.get("merchant_agent")

        transactions = []

        # Rent (Section 11.4)
        if state.archetype != "student" or rng.random() < 0.3:
            rent_txns = self._generate_rent(state, start_date, end_date)
            transactions.extend(rent_txns)

        # Utilities
        util_txns = self._generate_utilities(state, start_date, end_date)
        transactions.extend(util_txns)

        # Insurance
        ins_txns = self._generate_insurance(state, start_date, end_date)
        transactions.extend(ins_txns)

        # Phone/Internet
        phone_txns = self._generate_phone_internet(state, start_date, end_date)
        transactions.extend(phone_txns)

        # Update balance
        total = sum(t["amount"] for t in transactions)
        state.balance_proxy -= total
        state.update_liquidity()

        return {"obligation_transactions": transactions}

    def _generate_rent(self, state: UserState, start: date, end: date) -> list[dict]:
        rng = self.rng
        if state.housing_independence_state == "dependent" and rng.random() < 0.92:
            return []
        ratio = sample_beta(rng, *RENT_RATIO_BETA)
        ratio = max(RENT_RATIO_CAP[0], min(RENT_RATIO_CAP[1], ratio))
        if state.housing_independence_state == "shared":
            ratio *= 0.55
        rent_amount = Decimal(str(round(float(state.monthly_income) * ratio, 2)))

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
                txns.append(self._make_txn(pay_day, amount, landlord, "bills", state))

                # Late fee
                late_p = rng.uniform(*LATE_FEE_P)
                if state.liquidity in ("tight", "overdraft_risk", "overdrafted"):
                    late_p *= 3
                if rng.random() < late_p:
                    fee_date = pay_day + timedelta(days=int(rng.integers(5, 15)))
                    if fee_date <= end:
                        fee = Decimal(str(round(float(rng.uniform(25, 75)), 2)))
                        txns.append(self._make_txn(fee_date, fee, "LATE FEE - RENT", "bills", state))

            if current.month == 12:
                current = date(current.year + 1, 1, 1)
            else:
                current = date(current.year, current.month + 1, 1)

        return txns

    def _generate_utilities(self, state: UserState, start: date, end: date) -> list[dict]:
        rng = self.rng
        mean = rng.uniform(*UTILITY_AMOUNT_MEAN)
        std = rng.uniform(*UTILITY_AMOUNT_STD)
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
                    amount = sample_truncated_normal(rng, mean / n_utils, std, lo=20.0, hi=500.0)
                    amount *= seasonal
                    txns.append(self._make_txn(
                        pay_day, Decimal(str(round(amount, 2))),
                        util_name, cat, state
                    ))

            if current.month == 12:
                current = date(current.year + 1, 1, 1)
            else:
                current = date(current.year, current.month + 1, 1)

        return txns

    def _generate_insurance(self, state: UserState, start: date, end: date) -> list[dict]:
        rng = self.rng
        if state.archetype == "student" and rng.random() < 0.6:
            return []

        annual_premium = float(rng.uniform(800, 3000))
        is_monthly = rng.random() < INSURANCE_MONTHLY_P
        txns = []

        if is_monthly:
            monthly_amt = Decimal(str(round(annual_premium / 12, 2)))
            current = date(start.year, start.month, 1)
            while current <= end:
                pay_day = current.replace(day=min(28, int(rng.integers(1, 15))))
                if start <= pay_day <= end:
                    txns.append(self._make_txn(
                        pay_day, monthly_amt,
                        "INSURANCE PREMIUM", "bills", state
                    ))
                if current.month == 12:
                    current = date(current.year + 1, 1, 1)
                else:
                    current = date(current.year, current.month + 1, 1)
        else:
            # Semi-annual or annual lump sums
            for year in range(start.year, end.year + 1):
                for month in ([1, 7] if rng.random() < 0.5 else [3]):
                    pay_date = date(year, month, int(rng.integers(1, 15)))
                    if start <= pay_date <= end:
                        amt = Decimal(str(round(annual_premium / (2 if month != 3 else 1), 2)))
                        txns.append(self._make_txn(
                            pay_date, amt,
                            "INSURANCE PREMIUM ANNUAL", "bills", state
                        ))

        return txns

    def _generate_phone_internet(self, state: UserState, start: date, end: date) -> list[dict]:
        rng = self.rng
        txns = []
        phone_cost = Decimal(str(round(float(rng.uniform(40, 120)), 2)))
        internet_cost = Decimal(str(round(float(rng.uniform(50, 100)), 2)))

        current = date(start.year, start.month, 1)
        while current <= end:
            # Phone bill
            ph_day = current.replace(day=min(28, int(rng.integers(5, 20))))
            if start <= ph_day <= end:
                noise = Decimal(str(round(float(rng.normal(0, 3)), 2)))
                txns.append(self._make_txn(
                    ph_day, max(Decimal("20"), phone_cost + noise),
                    rng.choice(["T-MOBILE BILL", "AT&T WIRELESS", "VERIZON WIRELESS"]),
                    "bills", state
                ))

            # Internet bill
            inet_day = current.replace(day=min(28, int(rng.integers(10, 25))))
            if start <= inet_day <= end:
                noise = Decimal(str(round(float(rng.normal(0, 2)), 2)))
                txns.append(self._make_txn(
                    inet_day, max(Decimal("30"), internet_cost + noise),
                    rng.choice(["XFINITY INTERNET", "SPECTRUM", "ATT INTERNET"]),
                    "bills", state
                ))

            if current.month == 12:
                current = date(current.year + 1, 1, 1)
            else:
                current = date(current.year, current.month + 1, 1)

        return txns

    def _make_txn(self, txn_date: date, amount: Decimal, description: str,
                  category: str, state: UserState) -> dict:
        rng = self.rng
        hour = int(rng.integers(6, 12))
        minute = int(rng.integers(0, 60))
        occurred = make_aware_dt(
            datetime(txn_date.year, txn_date.month, txn_date.day, hour, minute)
        )

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
