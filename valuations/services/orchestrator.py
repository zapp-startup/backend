from __future__ import annotations

from users.services.state_orchestrator import recompute_user_state
from valuations.services.transaction_value_score import persist_transaction_value_score
from valuations.services.value_score_orchestrator import run_value_scores_for_user


def recompute_user_valuations(user_id: int, *, subscription_ids: list[int] | None = None):
    recompute_user_state(user_id)
    return run_value_scores_for_user(user_id, subscription_ids=subscription_ids)


def recompute_single_transaction(transaction):
    recompute_user_state(transaction.user_id)
    return persist_transaction_value_score(transaction)
