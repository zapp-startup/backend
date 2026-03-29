"""
Stochastic helpers: Beta, LogNormal, NegativeBinomial, Dirichlet,
payday/weekend/seasonality multipliers.
Sections 5.1-5.8 of the rules document.
"""

from __future__ import annotations

import math
from datetime import date, datetime

import numpy as np
from decimal import Decimal

from numpy.random import Generator

from datagen.config import (
    ARCHETYPE_PRIOR,
    ARCHETYPES,
    PAYDAY_ETA_RANGE,
    PAYDAY_TAU_RANGE,
    SEASONALITY,
    WEEKEND_MULTIPLIER,
)

# Cached timezone module to avoid repeated import lookup in make_aware_dt
_django_timezone = None


def _get_timezone():
    global _django_timezone
    if _django_timezone is None:
        try:
            from django.utils import timezone as tz
            _django_timezone = tz
        except Exception:
            _django_timezone = False
    return _django_timezone if _django_timezone else None


def make_aware_dt(dt: datetime) -> datetime:
    """Make datetime timezone-aware for Django. No-op if already aware."""
    if dt.tzinfo is not None:
        return dt
    tz = _get_timezone()
    if tz:
        return tz.make_aware(dt)
    return dt


def sample_archetype(rng: Generator) -> str:
    """Section 5.1: Sample user archetype from categorical prior."""
    probs = [ARCHETYPE_PRIOR[a] for a in ARCHETYPES]
    return ARCHETYPES[rng.choice(len(ARCHETYPES), p=probs)]


def sample_beta(rng: Generator, alpha: float, beta: float) -> float:
    """Sample from Beta(alpha, beta), return float in [0, 1]."""
    return float(rng.beta(alpha, beta))


def sample_lognormal(rng: Generator, mu: float, sigma: float) -> float:
    """Sample from LogNormal(mu, sigma), return positive float."""
    return float(rng.lognormal(mu, sigma))


def sample_lognormal_decimal(rng: Generator, mu: float, sigma: float) -> Decimal:
    """Sample from LogNormal and return as Decimal rounded to 2 places."""
    return Decimal(str(round(sample_lognormal(rng, mu, sigma), 2)))


def sample_truncated_normal(rng: Generator, mu: float, sigma: float,
                            lo: float = 0.0, hi: float = float("inf")) -> float:
    """Sample from Normal(mu, sigma) truncated to [lo, hi]."""
    for _ in range(100):
        val = float(rng.normal(mu, sigma))
        if lo <= val <= hi:
            return val
    return max(lo, min(hi, float(rng.normal(mu, sigma))))


def sample_negbin(rng: Generator, r: float, p: float) -> int:
    """Section 5.4: Negative binomial for overdispersed counts."""
    if r <= 0:
        return 0
    return int(rng.negative_binomial(max(1, round(r)), min(0.99, max(0.01, p))))


def sample_dirichlet(rng: Generator, alpha: list[float]) -> list[float]:
    """Section 5.3: Dirichlet distribution for budget shares."""
    return list(rng.dirichlet([max(0.01, a) for a in alpha]))


def sample_poisson(rng: Generator, lam: float) -> int:
    """Poisson draw, clamped to non-negative lambda."""
    return int(rng.poisson(max(0.01, lam)))


def payday_multiplier(days_since_payday: int, eta: float, tau: float) -> float:
    """Section 5.6: Discretionary spend multiplier near payday."""
    return 1.0 + eta * math.exp(-days_since_payday / tau)


def weekend_multiplier(day: date, category: str) -> float:
    """Section 5.7: Weekend boost for dining and entertainment."""
    if category not in ("dining", "entertainment"):
        return 1.0
    return WEEKEND_MULTIPLIER.get(day.weekday(), 1.0)


def seasonality_multiplier(month: int, category: str) -> float:
    """Section 5.8: Seasonal multiplier for a category and month (1-12)."""
    if category in SEASONALITY:
        return SEASONALITY[category][month - 1]
    return 1.0


def logistic(x: float) -> float:
    """Logistic sigmoid function."""
    return 1.0 / (1.0 + math.exp(-x))


def clip01(x: float) -> float:
    """Clamp to [0, 1]."""
    return max(0.0, min(1.0, x))


def sample_payday_params(rng: Generator, impulse: float) -> tuple[float, float]:
    """Sample user-specific payday effect parameters, scaled by impulse."""
    eta = rng.uniform(*PAYDAY_ETA_RANGE) * (0.5 + impulse)
    tau = rng.uniform(*PAYDAY_TAU_RANGE)
    return eta, tau


def make_rng(seed: int | None = None) -> Generator:
    """Create a reproducible numpy random generator."""
    return np.random.default_rng(seed)
