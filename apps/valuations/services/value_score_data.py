"""
Build pandas `data` dict for `value_score_model.ValueScoreModel.predict`.

When VALUE_SCORE_INCLUDE_FEEDBACK_NUMERICS is False (default), transaction-level
feedback columns are omitted so the feature pipeline uses neutral defaults until
a dedicated feedback→numeric pipeline exists.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
import pandas as pd
from django.conf import settings
from django.utils import timezone

from apps.subscriptions.models import Merchant, Subscription
from apps.transactions.models import Transaction, TransactionDirection
from apps.users.models import UserComputed, UserRawExplicit, UserRawInferred


def _monthly_equivalent(price: Decimal, billing_cycle: str) -> float:
    p = float(price)
    c = (billing_cycle or "monthly").lower()
    if c == "weekly":
        return p * 52.0 / 12.0
    if c == "yearly":
        return p / 12.0
    return p


def _empty_feedback_frame() -> pd.DataFrame:
    return pd.DataFrame(columns=["user_id", "merchant_id", "feedback_value_score", "feedback_confidence"])


def _transaction_frame_columns(include_tx_feedback: bool) -> list[str]:
    columns = ["id", "user_id", "merchant_id", "occurred_at", "amount", "direction"]
    if include_tx_feedback:
        columns.extend(
            [
                "satisfaction_rating",
                "regret_rating",
                "repurchase_likelihood",
                "impulse_score",
                "regret_score",
                "usage_frequency",
            ]
        )
    return columns


def build_value_score_dataframes(
    user_id: int,
    *,
    subscription_ids: list[int] | None = None,
    reference_time: datetime | None = None,
) -> dict[str, pd.DataFrame]:
    """
    Assemble merchants, subscriptions, transactions, user_explicit, user_computed, user_inferred.
    Scoped to one user; subscriptions default to all of that user's rows.
    """
    include_tx_feedback = bool(getattr(settings, "VALUE_SCORE_INCLUDE_FEEDBACK_NUMERICS", False))

    ref = reference_time or timezone.now()
    if timezone.is_naive(ref):
        ref = timezone.make_aware(ref, timezone.utc)

    sub_qs = Subscription.objects.filter(user_id=user_id).select_related("merchant")
    if subscription_ids is not None:
        sub_qs = sub_qs.filter(id__in=subscription_ids)
    subscriptions = list(sub_qs)
    if not subscriptions:
        return {
            "merchants": pd.DataFrame(columns=["id", "name", "category"]),
            "subscriptions": pd.DataFrame(
                columns=[
                    "id",
                    "user_id",
                    "merchant_id",
                    "price",
                    "started_on",
                    "billing_cycle",
                    "status",
                    "usage_frequency",
                    "reactivation_count",
                ]
            ),
            "transactions": pd.DataFrame(columns=["id", "user_id", "merchant_id", "occurred_at", "amount", "direction"]),
            "user_explicit": pd.DataFrame(),
            "user_computed": pd.DataFrame(),
            "user_inferred": pd.DataFrame(),
            "feedback_signals": _empty_feedback_frame(),
        }

    merchant_ids = {s.merchant_id for s in subscriptions}
    merchants = Merchant.objects.filter(id__in=merchant_ids)

    merchant_rows = []
    for m in merchants:
        merchant_rows.append(
            {
                "id": m.id,
                "name": m.name or "",
                "category": (m.category or "other").lower(),
            }
        )

    sub_rows = []
    for s in subscriptions:
        sub_rows.append(
            {
                "id": s.id,
                "user_id": user_id,
                "merchant_id": s.merchant_id,
                "price": float(s.price),
                "started_on": s.started_on.isoformat() if s.started_on else None,
                "billing_cycle": (s.billing_cycle or "monthly").lower(),
                "status": (s.status or "active").lower(),
                "usage_frequency": float(s.usage_frequency) if s.usage_frequency is not None else None,
                "reactivation_count": int(s.reactivation_count or 0),
                # Model preprocessor can use these when present; not the same as NLP feedback pipeline.
                "subscription_utilization": s.subscription_utilization,
                "subscription_cost_benefit": s.subscription_cost_benefit,
            }
        )

    # Transactions: all spend rows for this user tied to these merchants (model groups by user+merchant)
    tx_qs = Transaction.objects.filter(
        user_id=user_id,
        merchant_id__in=merchant_ids,
        direction=TransactionDirection.SPEND,
    ).order_by("occurred_at")

    tx_rows = []
    for t in tx_qs:
        row = {
            "id": t.id,
            "user_id": user_id,
            "merchant_id": t.merchant_id,
            "occurred_at": t.occurred_at.isoformat() if timezone.is_aware(t.occurred_at) else timezone.make_aware(t.occurred_at, timezone.utc).isoformat(),
            "amount": float(t.amount),
            "direction": t.direction,
        }
        if include_tx_feedback:
            row.update(
                {
                    "satisfaction_rating": t.satisfaction_rating,
                    "regret_rating": t.regret_rating,
                    "repurchase_likelihood": t.repurchase_likelihood,
                    "impulse_score": t.impulse_score,
                    "regret_score": t.regret_score,
                    "usage_frequency": t.usage_frequency,
                }
            )
        tx_rows.append(row)

    explicit = UserRawExplicit.objects.filter(user_id=user_id).first()
    computed = UserComputed.objects.filter(user_id=user_id).first()
    inferred = UserRawInferred.objects.filter(user_id=user_id).first()

    def _row_explicit(e: UserRawExplicit | None) -> dict:
        if not e:
            return {
                "user_id": user_id,
                "life_stage": "other",
                "financial_goal": "other",
                "monthly_income": 3000.0,
                "value_priority_cost": None,
                "value_priority_quality": None,
                "value_priority_sustainability": None,
                "risk_tolerance": "medium",
                "budget_style": "flexible",
            }
        return {
            "user_id": user_id,
            "life_stage": (e.life_stage or "other").lower(),
            "financial_goal": (e.financial_goal or "other").lower(),
            "monthly_income": float(e.monthly_income) if e.monthly_income is not None else 3000.0,
            "value_priority_cost": float(e.value_priority_cost) if e.value_priority_cost is not None else None,
            "value_priority_quality": float(e.value_priority_quality) if e.value_priority_quality is not None else None,
            "value_priority_sustainability": float(e.value_priority_sustainability)
            if e.value_priority_sustainability is not None
            else None,
            "risk_tolerance": (e.risk_tolerance or "medium").lower(),
            "budget_style": (e.budget_style or "flexible").lower(),
        }

    def _row_computed(c: UserComputed | None) -> dict:
        if not c:
            return {
                "user_id": user_id,
                "cost_weight": None,
                "quality_weight": None,
                "sustainability_weight": None,
                "impulse_susceptibility_score": None,
                "regret_sensitivity": None,
                "budget_adherence_score": None,
            }
        return {
            "user_id": user_id,
            "cost_weight": c.cost_weight,
            "quality_weight": c.quality_weight,
            "sustainability_weight": c.sustainability_weight,
            "impulse_susceptibility_score": c.impulse_susceptibility_score,
            "regret_sensitivity": c.regret_sensitivity,
            "budget_adherence_score": c.budget_adherence_score,
        }

    def _row_inferred(i: UserRawInferred | None) -> dict:
        if not i:
            return {
                "user_id": user_id,
                "percent_income_spent_on_subscriptions": None,
                "regret_frequency": None,
                "active_subscriptions_count": None,
            }
        return {
            "user_id": user_id,
            "percent_income_spent_on_subscriptions": i.percent_income_spent_on_subscriptions,
            "regret_frequency": i.regret_frequency,
            "active_subscriptions_count": float(i.active_subscriptions_count)
            if i.active_subscriptions_count is not None
            else None,
        }

    explicit_df = pd.DataFrame([_row_explicit(explicit)])
    computed_df = pd.DataFrame([_row_computed(computed)])
    inferred_df = pd.DataFrame([_row_inferred(inferred)])

    return {
        "merchants": pd.DataFrame(merchant_rows),
        "subscriptions": pd.DataFrame(sub_rows),
        "transactions": pd.DataFrame(tx_rows, columns=_transaction_frame_columns(include_tx_feedback)),
        "user_explicit": explicit_df,
        "user_computed": computed_df,
        "user_inferred": inferred_df,
        "feedback_signals": _empty_feedback_frame(),
    }


def score_to_financial_fields(
    subscription: Subscription,
    value_score: int,
    *,
    period_days: int = 30,
) -> tuple[Decimal, Decimal, Decimal]:
    """
    Map model score (0–150) to monetary fields required by SubscriptionValuation.
    Interpretation: estimated_value scales with score vs a 100 baseline; net = value - cost.
    """
    monthly_cost = Decimal(str(_monthly_equivalent(subscription.price, subscription.billing_cycle)))
    total_cost = (monthly_cost * Decimal(str(period_days)) / Decimal("30")).quantize(Decimal("0.01"))
    baseline = Decimal("100")
    vs = Decimal(min(max(int(value_score), 0), 150))
    estimated_value = (total_cost * vs / baseline).quantize(Decimal("0.01"))
    net_value = (estimated_value - total_cost).quantize(Decimal("0.01"))
    return total_cost, estimated_value, net_value
