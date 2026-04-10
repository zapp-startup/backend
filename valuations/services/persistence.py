from __future__ import annotations

from typing import Any

from django.utils import timezone

from valuations.models import SubscriptionValuation, TransactionValuation, ValuationModelVersion


def create_transaction_valuation_snapshot(
    *,
    transaction,
    model_version: ValuationModelVersion,
    context: str,
    value_score: int,
    base_value_score: int | None,
    confidence: float,
    tier_used: str,
    inference_status: str,
    evidence_json: dict[str, Any] | list[Any] | None,
    reasoning_json: dict[str, Any] | None = None,
) -> TransactionValuation:
    return TransactionValuation.objects.create(
        user_id=transaction.user_id,
        transaction=transaction,
        model_version=model_version,
        context=context,
        value_score=value_score,
        base_value_score=base_value_score,
        confidence=confidence,
        tier_used=tier_used,
        inference_status=inference_status,
        stale_at=None,
        evidence_json=evidence_json or {},
        reasoning_json=reasoning_json or {},
    )


def mark_transaction_valuations_stale(*, transaction_id: int | None = None, user_id: int | None = None) -> int:
    filters: dict[str, Any] = {}
    if transaction_id is not None:
        filters["transaction_id"] = transaction_id
    if user_id is not None:
        filters["user_id"] = user_id
    if not filters:
        return 0
    return TransactionValuation.objects.filter(**filters, stale_at__isnull=True).update(stale_at=timezone.now())


def latest_subscription_valuations_queryset(user):
    latest_ids = []
    seen: set[int] = set()
    for valuation in (
        SubscriptionValuation.objects.filter(user=user)
        .select_related("subscription", "model_version")
        .order_by("subscription_id", "-period_end", "-created_at")
    ):
        if valuation.subscription_id in seen:
            continue
        seen.add(valuation.subscription_id)
        latest_ids.append(valuation.id)
    return SubscriptionValuation.objects.filter(id__in=latest_ids).select_related("subscription", "model_version")


def latest_transaction_valuations_queryset(user):
    latest_ids = []
    seen: set[int] = set()
    for valuation in (
        TransactionValuation.objects.filter(user=user)
        .select_related("transaction", "model_version")
        .order_by("transaction_id", "-created_at")
    ):
        if valuation.transaction_id in seen:
            continue
        seen.add(valuation.transaction_id)
        latest_ids.append(valuation.id)
    return TransactionValuation.objects.filter(id__in=latest_ids).select_related("transaction", "model_version")
