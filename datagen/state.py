"""
UserState dataclass (latent vector Su) and liquidity transition logic.
Sections 4 and 18 of the rules document.
"""

from __future__ import annotations

import bisect
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from datagen.config import (
    DEBT_RECOVER_MONTHS,
    DEBT_WORSEN_MONTHS,
    LIQUIDITY_THRESHOLDS,
    SUBSCRIPTION_BURDEN_THRESHOLDS,
)


DEBT_CARRY_ORDER = ["none", "managed", "revolving", "stressed"]


@dataclass
class UserState:
    """Latent state vector Su for a single synthetic user."""

    archetype: str = ""
    liquidity: str = "stable"
    impulse: float = 0.5
    budget_adherence: float = 0.5
    regret_sensitivity: float = 0.5
    quality_preference: float = 0.5
    novelty_seeking: float = 0.5
    luxury_affinity: float = 0.5
    subscription_engagement: str = "moderate"
    household_pressure: float = 0.5
    credit_stress: float = 0.5

    monthly_income: Decimal = Decimal("0")
    monthly_fixed_expenses: Decimal = Decimal("0")
    balance_proxy: Decimal = Decimal("0")
    transaction_spend_target_ratio: float = 0.56

    category_budgets: dict[str, float] = field(default_factory=dict)
    last_paydays: list[date] = field(default_factory=list)

    household_size: int = 1
    dependents_count: int = 0
    age: int = 30

    payday_eta: float = 0.5
    payday_tau: float = 5.0

    # Extended latent dimensions (synthetic realism patch)
    debt_carry_state: str = "none"
    income_stability_state: str = "stable"
    price_sensitivity: float = 0.5
    subscription_burden_state: str = "light"
    housing_independence_state: str = "independent"

    _consecutive_deficit_months: int = 0
    _consecutive_surplus_months: int = 0
    large_purchase_shock_this_month: bool = False
    luxury_cooldown_days: int = 0

    def update_liquidity(self) -> None:
        """Section 18.1: Update liquidity state based on balance proxy vs fixed expenses."""
        if self.monthly_fixed_expenses <= 0:
            self.liquidity = "comfortable"
            return

        ratio = float(self.balance_proxy / self.monthly_fixed_expenses)
        thresholds = LIQUIDITY_THRESHOLDS

        if ratio > thresholds["comfortable"]:
            self.liquidity = "comfortable"
        elif ratio > thresholds["stable_lower"]:
            self.liquidity = "stable"
        elif ratio > 0:
            self.liquidity = "tight"
        elif ratio > -0.1:
            self.liquidity = "overdraft_risk"
        else:
            self.liquidity = "overdrafted"

    def liquidity_numeric(self) -> float:
        """Return a 0-1 numeric encoding of liquidity for logistic models."""
        return {
            "comfortable": 0.0,
            "stable": 0.25,
            "tight": 0.55,
            "overdraft_risk": 0.80,
            "overdrafted": 1.0,
        }.get(self.liquidity, 0.25)

    def days_since_payday(self, current_date: date) -> int:
        """Days since the most recent payday. Uses bisect for O(log n) lookup."""
        if not self.last_paydays:
            return 15
        # last_paydays is sorted ascending; bisect_right - 1 gives last elem <= current_date
        idx = bisect.bisect_right(self.last_paydays, current_date) - 1
        if idx < 0:
            return 15
        return (current_date - self.last_paydays[idx]).days

    def apply_trait_drift(self, rng) -> None:
        """Section 18.3: Slow mean-reversion drift on impulse and regret sensitivity."""
        rho = 0.98
        noise_std = 0.01
        self.impulse = max(0.0, min(1.0,
            rho * self.impulse + (1 - rho) * 0.5 + rng.normal(0, noise_std)))
        self.regret_sensitivity = max(0.0, min(1.0,
            rho * self.regret_sensitivity + (1 - rho) * 0.5 + rng.normal(0, noise_std)))

    def apply_engagement_transition(self, rng, low_usage_frac: float, regret_rate: float) -> None:
        """Section 18.2: Subscription engagement state transition."""
        p_churn = 0.02
        if self.liquidity in ("tight", "overdraft_risk", "overdrafted"):
            p_churn += 0.05
        if low_usage_frac > 0.5:
            p_churn += 0.04
        if regret_rate > 0.3:
            p_churn += 0.03

        if self.subscription_engagement != "churn_prone" and rng.random() < p_churn:
            self.subscription_engagement = "churn_prone"
        elif self.subscription_engagement == "churn_prone" and rng.random() < 0.15:
            self.subscription_engagement = "moderate"

    def update_subscription_burden(self, monthlyized_sub_cost: float) -> None:
        """Classify subscription burden from monthlyized cost / monthly take-home income."""
        mi = float(self.monthly_income)
        if mi <= 0:
            self.subscription_burden_state = "light"
            return
        ratio = monthlyized_sub_cost / mi
        t = SUBSCRIPTION_BURDEN_THRESHOLDS
        if ratio < t["light"]:
            self.subscription_burden_state = "light"
        elif ratio < t["normal"]:
            self.subscription_burden_state = "normal"
        elif ratio < t["stretched"]:
            self.subscription_burden_state = "stretched"
        else:
            self.subscription_burden_state = "overloaded"

    def apply_debt_transition(self, monthly_surplus: float) -> None:
        """
        Single source of truth for debt state changes from monthly cash flow persistence.
        Do not call from individual transactions; use month-end surplus only.
        """
        if monthly_surplus < 0:
            self._consecutive_deficit_months += 1
            self._consecutive_surplus_months = 0
            if self._consecutive_deficit_months >= DEBT_WORSEN_MONTHS:
                self._worsen_debt_one_step()
                self._consecutive_deficit_months = 0
        else:
            self._consecutive_surplus_months += 1
            self._consecutive_deficit_months = 0
            if self._consecutive_surplus_months >= DEBT_RECOVER_MONTHS:
                self._recover_debt_one_step()
                self._consecutive_surplus_months = 0

    def _worsen_debt_one_step(self) -> None:
        idx = DEBT_CARRY_ORDER.index(self.debt_carry_state) if self.debt_carry_state in DEBT_CARRY_ORDER else 0
        if idx < len(DEBT_CARRY_ORDER) - 1:
            self.debt_carry_state = DEBT_CARRY_ORDER[idx + 1]

    def _recover_debt_one_step(self) -> None:
        idx = DEBT_CARRY_ORDER.index(self.debt_carry_state) if self.debt_carry_state in DEBT_CARRY_ORDER else 0
        if idx > 0:
            self.debt_carry_state = DEBT_CARRY_ORDER[idx - 1]

    def income_stability_numeric(self) -> float:
        """0=stable, 1=fragile for reserve scaling."""
        return {"stable": 0.0, "variable": 0.5, "fragile": 1.0}.get(
            self.income_stability_state, 0.25
        )

    def debt_capacity_monthly(self) -> float:
        """Small credit allowance from revolving debt (fraction of income)."""
        return {
            "none": 0.0,
            "managed": 0.02,
            "revolving": 0.05,
            "stressed": 0.08,
        }.get(self.debt_carry_state, 0.0) * float(self.monthly_income or 0)
