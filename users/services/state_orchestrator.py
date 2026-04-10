from __future__ import annotations

from django.db import transaction

from valuations.models import ValuationDirtyState

from .state_computed import recompute_user_computed
from .state_inference import recompute_user_raw_inferred


def mark_user_state_dirty(
    user_id: int,
    *,
    reason: str,
    transaction_id: int | None = None,
    subscription_id: int | None = None,
    priority: int = 5,
) -> ValuationDirtyState:
    return ValuationDirtyState.objects.create(
        user_id=user_id,
        transaction_id=transaction_id,
        subscription_id=subscription_id,
        dirty_reason=reason,
        priority=priority,
    )


@transaction.atomic
def recompute_user_state(user_id: int, *, window_days: int = 90):
    inferred = recompute_user_raw_inferred(user_id, window_days=window_days)
    computed = recompute_user_computed(user_id)
    ValuationDirtyState.objects.filter(
        user_id=user_id,
        transaction_id__isnull=True,
        subscription_id__isnull=True,
    ).delete()
    return inferred, computed
