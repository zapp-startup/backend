from __future__ import annotations

from subscriptions.models import Subscription
from users.services.state_orchestrator import mark_user_state_dirty


def mark_subscription_dirty(subscription: Subscription, *, reason: str, priority: int = 5) -> None:
    mark_user_state_dirty(
        subscription.user_id,
        reason=reason,
        subscription_id=subscription.id,
        priority=priority,
    )
