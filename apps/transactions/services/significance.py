from __future__ import annotations

from django.db.models import Avg

from apps.transactions.models import Transaction, TransactionCategory, TransactionDirection


HIGH_SIGNAL_CATEGORIES = {
    TransactionCategory.SHOPPING,
    TransactionCategory.ENTERTAINMENT,
    TransactionCategory.EDUCATION,
    TransactionCategory.HEALTH,
    TransactionCategory.EATING_OUT,
    TransactionCategory.SUBSCRIPTIONS,
}


def is_significant_transaction(transaction: Transaction) -> bool:
    if transaction.direction != TransactionDirection.SPEND:
        return False
    if transaction.subscription_id:
        return True
    if transaction.category in HIGH_SIGNAL_CATEGORIES:
        return True
    avg_amount = (
        Transaction.objects.filter(
            user_id=transaction.user_id,
            direction=TransactionDirection.SPEND,
        ).aggregate(avg_amount=Avg("amount"))["avg_amount"]
    )
    if avg_amount is None:
        return float(transaction.amount) >= 20.0
    return float(transaction.amount) >= max(float(avg_amount) * 0.5, 20.0)
