"""
UserState dataclass (latent vector Su) and liquidity transition logic.
Sections 4 and 18 of the rules document.
"""

from __future__ import annotations

import bisect
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from datagen.config import LIQUIDITY_THRESHOLDS


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
    subscription_engagement: str = "moderate"
    household_pressure: float = 0.5
    credit_stress: float = 0.5

    monthly_income: Decimal = Decimal("0")
    monthly_fixed_expenses: Decimal = Decimal("0")
    balance_proxy: Decimal = Decimal("0")

    category_budgets: dict[str, float] = field(default_factory=dict)
    last_paydays: list[date] = field(default_factory=list)

    household_size: int = 1
    dependents_count: int = 0
    age: int = 30

    payday_eta: float = 0.5
    payday_tau: float = 5.0

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
