from __future__ import annotations

from collections import Counter
from datetime import timedelta
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from apps.subscriptions.models import Subscription, SubscriptionStatus
from apps.transactions.models import Transaction, TransactionDirection, TransactionReflection
from apps.users.models import UserRawExplicit, UserRawInferred

from .formulas import FEATURE_LOGIC_VERSION, clamp, monthlyized_amount


def _behavioral_transactions(user_id: int, *, window_days: int) -> list[Transaction]:
    since = timezone.now() - timedelta(days=window_days)
    return list(
        Transaction.objects.filter(
            user_id=user_id,
            direction=TransactionDirection.SPEND,
            occurred_at__gte=since,
        ).select_related("merchant")
    )


def _reflection_flags(user_id: int, transaction_ids: list[int]) -> dict[int, bool]:
    if not transaction_ids:
        return {}
    reflections = TransactionReflection.objects.filter(user_id=user_id, transaction_id__in=transaction_ids)
    result: dict[int, bool] = {}
    for reflection in reflections:
        result[reflection.transaction_id] = reflection.was_worth_it is False
    return result


@transaction.atomic
def recompute_user_raw_inferred(user_id: int, *, window_days: int = 90) -> UserRawInferred:
    now = timezone.now()
    txns = _behavioral_transactions(user_id, window_days=window_days)
    subscriptions = list(Subscription.objects.filter(user_id=user_id))
    explicit = UserRawExplicit.objects.filter(user_id=user_id).first()

    txn_ids = [txn.id for txn in txns]
    reflection_flags = _reflection_flags(user_id, txn_ids)
    sparse_signals: dict[str, object] = {
        "transaction_count": len(txns),
        "subscription_count": len(subscriptions),
    }

    spend_amounts = [float(txn.amount) for txn in txns]
    avg_purchase_price = None
    purchase_price_variance = None
    actual_monthly_spending = Decimal("0.00")
    if spend_amounts:
        avg = sum(spend_amounts) / len(spend_amounts)
        avg_purchase_price = Decimal(str(round(avg, 2)))
        if len(spend_amounts) > 1:
            variance = sum((amount - avg) ** 2 for amount in spend_amounts) / (len(spend_amounts) - 1)
            purchase_price_variance = Decimal(str(round(variance, 6)))
        else:
            purchase_price_variance = Decimal("0.00")
        actual_monthly_spending = Decimal(str(round(sum(spend_amounts) / max(window_days / 30.0, 1.0), 2)))

    category_counts = Counter(txn.category for txn in txns if txn.category)
    category_distribution = {
        category: round(count / len(txns), 3) for category, count in category_counts.items()
    } if txns else {}

    impulsive_flags = []
    regret_flags = []
    late_night_count = 0
    decision_times = []
    merchant_counts = Counter()

    for txn in txns:
        merchant_key = txn.merchant_id or txn.description_raw or str(txn.id)
        merchant_counts[str(merchant_key)] += 1
        local_dt = timezone.localtime(txn.occurred_at) if timezone.is_aware(txn.occurred_at) else txn.occurred_at
        if local_dt.hour >= 22 or local_dt.hour < 5:
            late_night_count += 1

        # Compute this transaction's decision time separately before appending
        # so that quick_decision refers to THIS transaction, not the previous one.
        this_decision_minutes: float | None = None
        if txn.considered_at and txn.occurred_at:
            diff_minutes = (txn.occurred_at - txn.considered_at).total_seconds() / 60.0
            # Only trust the value when considered_at is genuinely before
            # occurred_at (positive diff) and not implausibly large.
            # considered_at values >= occurred_at indicate the live feedback form
            # mistakenly set it to submission time; state_inference ignores them.
            if 0 < diff_minutes < 10000:
                this_decision_minutes = diff_minutes
                decision_times.append(diff_minutes)

        researched = txn.self_report_researched is True
        # quick_decision is now scoped to THIS transaction's interval, not the
        # last appended value from a previous iteration.
        quick_decision = bool(
            this_decision_minutes is not None and this_decision_minutes <= 15
        )
        impulsive = bool(
            (txn.impulse_score is not None and float(txn.impulse_score) >= 0.5)
            or (txn.considered_at is None and not researched)
            or ((local_dt.hour >= 22 or local_dt.hour < 5) and quick_decision)
        )
        impulsive_flags.append(impulsive)

        regretful = bool(
            (txn.regret_rating is not None and int(txn.regret_rating) >= 50)
            or (txn.regret_score is not None and float(txn.regret_score) >= 0.25)
            or reflection_flags.get(txn.id, False)
        )
        if txn.regret_rating is not None or txn.regret_score is not None or txn.id in reflection_flags:
            regret_flags.append(regretful)

    percent_impulsive = clamp(sum(1 for flag in impulsive_flags if flag) / len(impulsive_flags)) if impulsive_flags else None
    regret_frequency = clamp(sum(1 for flag in regret_flags if flag) / len(regret_flags)) if regret_flags else None
    brand_repetition_rate = None
    if merchant_counts:
        brand_repetition_rate = clamp(max(merchant_counts.values()) / max(len(txns), 1))

    late_night_purchase_frequency = clamp(late_night_count / len(txns)) if txns else None
    avg_decision_time_minutes = round(sum(decision_times) / len(decision_times), 2) if decision_times else None

    active_subscriptions = [sub for sub in subscriptions if sub.status == SubscriptionStatus.ACTIVE]
    total_subscription_cost_value = round(
        sum(monthlyized_amount(sub.price, sub.billing_cycle) for sub in active_subscriptions), 2
    )
    total_subscription_cost = Decimal(str(total_subscription_cost_value))
    monthly_income = float(explicit.monthly_income) if explicit and explicit.monthly_income is not None else None

    # Only compute percent when both income and subscription cost data are usable.
    # If active subscriptions exist but ALL have null prices, the total cost is
    # spuriously 0 — keep the result null (unknown) rather than reporting 0 %,
    # which would be a false "no subscription spending" signal.
    has_priced_subs = any(sub.price is not None for sub in active_subscriptions)
    if not monthly_income or monthly_income <= 0:
        percent_income_spent_on_subscriptions = None
    elif active_subscriptions and not has_priced_subs:
        # Active subscriptions present but none have a known price.
        percent_income_spent_on_subscriptions = None
    else:
        percent_income_spent_on_subscriptions = clamp(total_subscription_cost_value / monthly_income)
    subscription_usage_frequency_json = {}
    for sub in active_subscriptions:
        if sub.usage_frequency is None:
            continue
        if sub.usage_frequency >= 5:
            bucket = "daily"
        elif sub.usage_frequency >= 2:
            bucket = "weekly"
        else:
            bucket = "monthly"
        subscription_usage_frequency_json[str(sub.id)] = bucket
    cancel_reactivation_frequency = (
        clamp(sum(1 for sub in subscriptions if (sub.reactivation_count or 0) > 0) / len(subscriptions))
        if subscriptions
        else None
    )

    if len(txns) >= 20:
        data_tier = "full"
    elif len(txns) >= 5:
        data_tier = "partial"
    else:
        data_tier = "cold_start"
    sparse_signals["missing_income"] = monthly_income is None
    sparse_signals["has_feedback"] = any(
        txn.regret_rating is not None or txn.satisfaction_rating is not None or txn.id in reflection_flags for txn in txns
    )

    obj, _ = UserRawInferred.objects.update_or_create(
        user_id=user_id,
        defaults={
            "window_days": window_days,
            "feature_logic_version": FEATURE_LOGIC_VERSION,
            "data_sufficiency_tier": data_tier,
            "sparse_signals_json": sparse_signals,
            "avg_purchase_price": avg_purchase_price,
            "purchase_price_variance": purchase_price_variance,
            "category_distribution_json": category_distribution,
            "percent_impulsive_purchases": percent_impulsive,
            "regret_frequency": regret_frequency,
            "brand_repetition_rate": brand_repetition_rate,
            "late_night_purchase_frequency": late_night_purchase_frequency,
            "avg_decision_time_minutes": avg_decision_time_minutes,
            "active_subscriptions_count": len(active_subscriptions),
            "total_subscription_cost": total_subscription_cost,
            "percent_income_spent_on_subscriptions": percent_income_spent_on_subscriptions,
            "subscription_usage_frequency_json": subscription_usage_frequency_json,
            "cancel_reactivation_frequency": cancel_reactivation_frequency,
            "actual_monthly_spending": actual_monthly_spending,
            "computed_at": now,
        },
    )
    return obj
