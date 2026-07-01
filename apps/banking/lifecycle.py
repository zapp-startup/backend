"""
Deletion and retention helpers for Plaid-linked data stored in Django.
"""
from __future__ import annotations

import logging

from django.db import transaction

from apps.banking.models import BankAccount, BankConnection, BankTransaction

logger = logging.getLogger(__name__)


def purge_user_bank_data(user) -> dict:
    """
    Delete all Plaid-derived rows for a user (connections cascade accounts;
    transactions deleted explicitly for clarity and logging).

    Does **not** call Plaid's item removal API — do that separately if your
    compliance program requires revoking access at Plaid when closing accounts.

    Returns counts for audit logging. Does not delete User or consent records
    (consent may be retained for legal/regulatory periods — configure separately).
    """
    uid = user.pk
    connections = list(BankConnection.objects.filter(user=user))
    conn_ids = [c.id for c in connections]

    txn_qs = BankTransaction.objects.filter(user=user)
    txn_count = txn_qs.count()
    account_count = BankAccount.objects.filter(connection_id__in=conn_ids).count() if conn_ids else 0

    with transaction.atomic():
        txn_qs.delete()
        BankAccount.objects.filter(connection_id__in=conn_ids).delete()
        deleted_conn = BankConnection.objects.filter(user=user).delete()

    logger.info(
        "banking_purge_completed user_id=%s connections_deleted=%s accounts=%s transactions=%s",
        uid,
        deleted_conn[0],
        account_count,
        txn_count,
    )
    return {
        "user_id": uid,
        "connections_removed": deleted_conn[0],
        "accounts_removed": account_count,
        "transactions_removed": txn_count,
    }


def tombstone_connection(connection: BankConnection) -> None:
    """Mark connection disconnected; caller may delete after retention window."""
    connection.status = BankConnection.Status.DISCONNECTED
    connection.save(update_fields=["status", "updated_at"])
