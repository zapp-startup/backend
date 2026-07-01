from __future__ import annotations

from django.db import transaction

from apps.users.models import UserComputed, UserRawExplicit, UserRawInferred

from .formulas import (
    FEATURE_LOGIC_VERSION,
    bucket_spending_personality,
    bucket_spending_style,
    bucket_subscription_behavior,
    clamp,
    normalize_weights,
)


@transaction.atomic
def recompute_user_computed(user_id: int) -> UserComputed:
    explicit = UserRawExplicit.objects.filter(user_id=user_id).first()
    inferred = UserRawInferred.objects.filter(user_id=user_id).first()

    cost_weight, quality_weight, sustainability_weight = normalize_weights(
        getattr(explicit, "value_priority_cost", None),
        getattr(explicit, "value_priority_quality", None),
        getattr(explicit, "value_priority_sustainability", None),
    )

    research_habit = getattr(explicit, "self_report_research_habit", None)
    late_night = getattr(inferred, "late_night_purchase_frequency", None)
    impulsive = getattr(inferred, "percent_impulsive_purchases", None)
    decision_minutes = getattr(inferred, "avg_decision_time_minutes", None)
    decision_component = None
    if decision_minutes is not None:
        decision_component = clamp(1.0 - min(decision_minutes, 120.0) / 120.0)
    impulse_susceptibility_score = clamp(
        (
            (impulsive or 0.0) * 0.45
            + (late_night or 0.0) * 0.2
            + (decision_component or 0.0) * 0.2
            + (clamp(1.0 - (research_habit or 5) / 10.0) or 0.0) * 0.15
        )
    )

    regret_sensitivity = clamp(
        (getattr(inferred, "regret_frequency", None) or 0.0) * 0.7
        + (decision_component or 0.0) * 0.1
        + ((1.0 - (getattr(inferred, "brand_repetition_rate", None) or 0.0)) * 0.2)
    )

    monthly_income = float(explicit.monthly_income) if explicit and explicit.monthly_income is not None else None
    fixed_expenses = float(explicit.monthly_fixed_expenses) if explicit and explicit.monthly_fixed_expenses is not None else 0.0
    actual_monthly_spending = float(inferred.actual_monthly_spending) if inferred and inferred.actual_monthly_spending is not None else None
    budget_capacity = None
    if monthly_income is not None:
        budget_capacity = max(monthly_income - fixed_expenses, 1.0)
    overspend_ratio = None
    if budget_capacity is not None and actual_monthly_spending is not None:
        overspend_ratio = max(actual_monthly_spending - budget_capacity, 0.0) / budget_capacity
    style = (explicit.budget_style or "").lower() if explicit else ""
    tolerance = 0.15 if style == "flexible" else 0.05 if style == "strict" else 0.1
    budget_adherence_score = 1.0 if overspend_ratio is None else clamp(1.0 - max(overspend_ratio - tolerance, 0.0))

    product_spending_style = bucket_spending_style(impulse_susceptibility_score, research_habit)
    subscription_behavior_type = bucket_subscription_behavior(
        getattr(inferred, "active_subscriptions_count", None),
        getattr(inferred, "percent_income_spent_on_subscriptions", None),
        getattr(inferred, "cancel_reactivation_frequency", None),
    )
    spending_personality = bucket_spending_personality(
        impulse_susceptibility_score,
        regret_sensitivity,
        budget_adherence_score,
    )

    obj, _ = UserComputed.objects.update_or_create(
        user_id=user_id,
        defaults={
            "feature_logic_version": FEATURE_LOGIC_VERSION,
            "computed_from_inferred_at": getattr(inferred, "computed_at", None),
            "spending_personality": spending_personality,
            "product_spending_style": product_spending_style,
            "subscription_behavior_type": subscription_behavior_type,
            "cost_weight": cost_weight,
            "quality_weight": quality_weight,
            "sustainability_weight": sustainability_weight,
            "impulse_susceptibility_score": impulse_susceptibility_score,
            "regret_sensitivity": regret_sensitivity,
            "budget_adherence_score": budget_adherence_score,
        },
    )
    return obj
