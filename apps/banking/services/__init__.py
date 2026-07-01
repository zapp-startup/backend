try:
    from .plaid_service import (
        create_link_token_for_user,
        exchange_public_token_for_user,
        fetch_accounts_for_connection,
        sync_transactions_for_connection,
        upsert_accounts_from_plaid,
        upsert_transactions_from_plaid,
    )
except ModuleNotFoundError as exc:
    if exc.name != "plaid":
        raise

    def _missing_plaid(*_args, **_kwargs):
        raise RuntimeError("Plaid support is unavailable because the 'plaid' package is not installed.")

    create_link_token_for_user = _missing_plaid
    exchange_public_token_for_user = _missing_plaid
    fetch_accounts_for_connection = _missing_plaid
    sync_transactions_for_connection = _missing_plaid
    upsert_accounts_from_plaid = _missing_plaid
    upsert_transactions_from_plaid = _missing_plaid

__all__ = [
    "create_link_token_for_user",
    "exchange_public_token_for_user",
    "fetch_accounts_for_connection",
    "sync_transactions_for_connection",
    "upsert_accounts_from_plaid",
    "upsert_transactions_from_plaid",
]
