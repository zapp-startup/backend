from __future__ import annotations


def clip_unit_interval(value: float | None) -> float | None:
    if value is None:
        return None
    return round(max(0.0, min(1.0, float(value))), 4)


def compute_subscription_cost_benefit(estimated_value, total_cost) -> float | None:
    if estimated_value is None or total_cost is None:
        return None

    estimated = float(estimated_value)
    cost = float(total_cost)
    denom = estimated + cost
    if denom <= 0:
        return 0.0
    return clip_unit_interval(estimated / denom)
