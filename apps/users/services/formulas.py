from __future__ import annotations

from decimal import Decimal


FEATURE_LOGIC_VERSION = "v1"


def clamp(value: float | None, minimum: float = 0.0, maximum: float = 1.0) -> float | None:
    if value is None:
        return None
    return max(minimum, min(maximum, float(value)))


def monthlyized_amount(price: Decimal | float | int | None, billing_cycle: str | None) -> float:
    if price is None:
        return 0.0
    amount = float(price)
    cycle = (billing_cycle or "monthly").lower()
    if cycle == "weekly":
        return amount * 52.0 / 12.0
    if cycle == "yearly":
        return amount / 12.0
    return amount


def normalize_weights(*values: float | None) -> tuple[float, ...]:
    cleaned = [max(0.0, float(v)) if v is not None else 0.0 for v in values]
    total = sum(cleaned)
    if total <= 0:
        return (0.5, 0.4, 0.1)[: len(cleaned)]
    return tuple(v / total for v in cleaned)


def bucket_spending_style(impulse_susceptibility_score: float | None, research_habit: int | None) -> str:
    score = impulse_susceptibility_score or 0.0
    habit = research_habit if research_habit is not None else 5
    if score >= 0.66 and habit <= 4:
        return "impulsive"
    if score <= 0.33 and habit >= 7:
        return "planned"
    return "mixed"


def bucket_subscription_behavior(
    active_subscriptions_count: int | None,
    subscription_burden: float | None,
    cancel_reactivation_frequency: float | None,
) -> str:
    active = active_subscriptions_count or 0
    burden = subscription_burden or 0.0
    churn = cancel_reactivation_frequency or 0.0
    if churn >= 0.4:
        return "churn-heavy"
    if active >= 6 or burden >= 0.2:
        return "over-subscribed"
    return "stable"


def bucket_spending_personality(
    impulse_susceptibility_score: float | None,
    regret_sensitivity: float | None,
    budget_adherence_score: float | None,
) -> str:
    impulse = impulse_susceptibility_score or 0.0
    regret = regret_sensitivity or 0.0
    budget = budget_adherence_score or 0.0
    if impulse >= 0.66 and budget < 0.5:
        return "reactive_spender"
    if regret >= 0.66:
        return "cautious_reviewer"
    if budget >= 0.7:
        return "disciplined_optimizer"
    return "balanced"
