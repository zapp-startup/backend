from __future__ import annotations

from transactions.models import Transaction
from users.services.state_orchestrator import mark_user_state_dirty


def mark_transaction_dirty(transaction: Transaction, *, reason: str, priority: int = 5) -> None:
    mark_user_state_dirty(
        transaction.user_id,
        reason=reason,
        transaction_id=transaction.id,
        subscription_id=transaction.subscription_id,
        priority=priority,
    )
