from __future__ import annotations


def clip_unit_interval(value: float | None) -> float | None:
    if value is None:
        return None
    return round(max(0.0, min(1.0, float(value))), 4)


def compute_subscription_cost_benefit(
    estimated_value,
    total_cost,
    *,
    adjustment_penalty: float = 0.0,
    spread_gain: float = 1.0,
    spread_center: float = 0.5,
) -> float | None:
    if estimated_value is None or total_cost is None:
        return None

    estimated = float(estimated_value)
    cost = float(total_cost)
    denom = estimated + cost
    if denom <= 0:
        return 0.0

    raw_score = estimated / denom
    adjusted = spread_center + (raw_score - spread_center) * float(spread_gain)
    adjusted -= float(adjustment_penalty)
    return clip_unit_interval(adjusted)
