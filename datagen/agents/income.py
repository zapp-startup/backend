"""
Agent 2: Income Agent (Section 8)
Generates realistic inflow behavior:
  - Salaried biweekly: every 14 days
  - Salaried monthly: 1st or last business day
  - Hourly: weekly or biweekly
  - Gig: irregular multiple deposits per week
  - Student: term-start lump + small job inflows
Writes to transactions_transaction with direction=income.
"""

from __future__ import annotations

import math
from datetime import date, datetime, timedelta
from decimal import Decimal

from datagen.agents.base import BaseAgent
from datagen.config import GIG_DEPOSITS_PER_WEEK_LAMBDA, INCOME_NOISE_SIGMA, INCOME_SHOCK_MONTHLY_P
from datagen.distributions import make_aware_dt, sample_lognormal, sample_poisson, sample_truncated_normal
from datagen.state import UserState


def _last_business_day(year: int, month: int) -> date:
    """Find last weekday of a given month."""
    if month == 12:
        d = date(year + 1, 1, 1) - timedelta(days=1)
    else:
        d = date(year, month + 1, 1) - timedelta(days=1)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d


class IncomeAgent(BaseAgent):
    """Generate income transactions for a date range."""

    def run(self, state: UserState, context: dict) -> dict:
        rng = self.rng
        start_date: date = context["start_date"]
        end_date: date = context["end_date"]
        arch = state.archetype
        monthly_income = float(state.monthly_income)

        stab_mult = {"stable": 1.0, "variable": 1.85, "fragile": 2.6}.get(
            state.income_stability_state, 1.0
        )
        noise_sigma = float(rng.uniform(*INCOME_NOISE_SIGMA)) * stab_mult
        transactions = []

        if arch in ("salary_biweekly", "hourly_weekly"):
            transactions = self._generate_periodic(
                state, start_date, end_date, monthly_income,
                period_days=14 if arch == "salary_biweekly" else 7,
                divisor=26 if arch == "salary_biweekly" else 52,
                noise_sigma=noise_sigma,
            )
        elif arch == "salary_monthly":
            transactions = self._generate_monthly_salary(
                state, start_date, end_date, monthly_income, noise_sigma
            )
        elif arch == "gig":
            transactions = self._generate_gig(
                state, start_date, end_date, monthly_income, noise_sigma
            )
        elif arch == "student":
            transactions = self._generate_student(
                state, start_date, end_date, monthly_income, noise_sigma
            )
        elif arch == "retired":
            transactions = self._generate_monthly_salary(
                state, start_date, end_date, monthly_income, noise_sigma
            )
        else:
            transactions = self._generate_periodic(
                state, start_date, end_date, monthly_income,
                period_days=14, divisor=26, noise_sigma=noise_sigma,
            )

        # Income shocks (Section 8.5)
        shock_p = float(rng.uniform(*INCOME_SHOCK_MONTHLY_P))
        months_span = max(1, (end_date - start_date).days // 30)
        for _ in range(months_span):
            if rng.random() < shock_p:
                shock_date = start_date + timedelta(days=int(rng.integers(0, (end_date - start_date).days + 1)))
                shock_type = rng.choice(["bonus", "missed", "reduced", "reimbursement"])
                if shock_type == "bonus":
                    amount = Decimal(str(round(monthly_income * float(rng.uniform(0.1, 0.5)), 2)))
                    transactions.append(self._make_txn(shock_date, amount, "Bonus payment", state))
                elif shock_type == "reimbursement":
                    amount = Decimal(str(round(float(rng.uniform(50, 500)), 2)))
                    transactions.append(self._make_txn(shock_date, amount, "Reimbursement", state))

        # Record paydays in state
        state.last_paydays = sorted(set(
            t["occurred_at"].date() if isinstance(t["occurred_at"], datetime) else t["occurred_at"]
            for t in transactions
        ))

        # Update balance with total income
        total_income = sum(t["amount"] for t in transactions)
        state.balance_proxy += total_income
        state.update_liquidity()

        return {"income_transactions": transactions}

    def _generate_periodic(self, state: UserState, start: date, end: date,
                           monthly: float, period_days: int, divisor: int,
                           noise_sigma: float) -> list[dict]:
        rng = self.rng
        paycheck = 12 * monthly / divisor
        txns = []
        current = start
        # Align to a plausible first payday
        offset = int(rng.integers(0, period_days))
        current = start + timedelta(days=offset)

        while current <= end:
            eps = float(rng.normal(0, noise_sigma))
            amount = Decimal(str(round(paycheck * (1 + eps), 2)))
            if amount > 0:
                txns.append(self._make_txn(current, amount, "Direct Deposit - Payroll", state))
            current += timedelta(days=period_days)
        return txns

    def _generate_monthly_salary(self, state: UserState, start: date, end: date,
                                 monthly: float, noise_sigma: float) -> list[dict]:
        rng = self.rng
        txns = []
        current = date(start.year, start.month, 1)
        use_first = rng.random() < 0.5

        while current <= end:
            if use_first:
                pay_date = current.replace(day=1)
                if pay_date.weekday() >= 5:
                    pay_date += timedelta(days=(7 - pay_date.weekday()))
            else:
                pay_date = _last_business_day(current.year, current.month)

            if start <= pay_date <= end:
                eps = float(rng.normal(0, noise_sigma))
                amount = Decimal(str(round(monthly * (1 + eps), 2)))
                if amount > 0:
                    txns.append(self._make_txn(pay_date, amount, "Direct Deposit - Salary", state))

            if current.month == 12:
                current = date(current.year + 1, 1, 1)
            else:
                current = date(current.year, current.month + 1, 1)
        return txns

    def _generate_gig(self, state: UserState, start: date, end: date,
                      monthly: float, noise_sigma: float) -> list[dict]:
        rng = self.rng
        txns = []
        # Weekly target income
        weekly_target = monthly / 4.33
        current = start

        while current <= end:
            week_end = min(current + timedelta(days=7), end)
            n_deposits = sample_poisson(rng, GIG_DEPOSITS_PER_WEEK_LAMBDA)
            for _ in range(n_deposits):
                dep_date = current + timedelta(days=int(rng.integers(0, (week_end - current).days + 1)))
                # LogNormal amount centered around weekly_target / n_deposits
                per_deposit = max(weekly_target / max(1, n_deposits), 30)
                mu = max(2.0, math.log(per_deposit))
                amount = Decimal(str(round(float(rng.lognormal(mu, 0.4)), 2)))
                if amount > 0:
                    desc = rng.choice([
                        "Payment from client", "Gig payout", "Freelance payment",
                        "Service payment", "Contract payment",
                    ])
                    txns.append(self._make_txn(dep_date, amount, desc, state))
            current = week_end + timedelta(days=1)
        return txns

    def _generate_student(self, state: UserState, start: date, end: date,
                          monthly: float, noise_sigma: float) -> list[dict]:
        rng = self.rng
        txns = []

        # Term-start financial aid / loans (Aug/Jan)
        for year in range(start.year, end.year + 1):
            for month in [1, 8]:
                term_date = date(year, month, int(rng.integers(1, 15)))
                if start <= term_date <= end:
                    amount = Decimal(str(round(monthly * float(rng.uniform(3, 6)), 2)))
                    txns.append(self._make_txn(term_date, amount, "Financial Aid Disbursement", state))

        # Small part-time job income (biweekly)
        job_income = monthly * 0.4
        current = start + timedelta(days=int(rng.integers(0, 14)))
        while current <= end:
            eps = float(rng.normal(0, noise_sigma))
            amount = Decimal(str(round(job_income * 12 / 26 * (1 + eps), 2)))
            if amount > 0:
                txns.append(self._make_txn(current, amount, "Part-time payroll", state))
            current += timedelta(days=14)

        return txns

    def _make_txn(self, txn_date: date, amount: Decimal, description: str,
                  state: UserState) -> dict:
        rng = self.rng
        hour = int(rng.integers(7, 18))
        minute = int(rng.integers(0, 60))
        occurred_at = make_aware_dt(
            datetime(txn_date.year, txn_date.month, txn_date.day, hour, minute)
        )

        return {
            "direction": "income",
            "amount": abs(amount),
            "occurred_at": occurred_at,
            "category": "other",
            "payment_channel": "bank",
            "description_raw": description.upper(),
            "merchant_obj": None,
            "subscription_obj": None,
        }
