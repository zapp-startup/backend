"""
Plaid service layer. Centralizes all Plaid API logic.
Reads configuration from Django settings (populated from env at startup).
"""
import logging
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from django.conf import settings

import plaid
from plaid.api import plaid_api
from plaid.model.accounts_get_request import AccountsGetRequest
from plaid.model.country_code import CountryCode
from plaid.model.item_public_token_exchange_request import ItemPublicTokenExchangeRequest
from plaid.model.link_token_create_request import LinkTokenCreateRequest
from plaid.model.link_token_create_request_user import LinkTokenCreateRequestUser
from plaid.model.products import Products
from plaid.model.transactions_sync_request import TransactionsSyncRequest

from banking.models import BankAccount, BankConnection, BankTransaction

logger = logging.getLogger(__name__)


def _to_dict(obj):
    """Convert Plaid response to dict if it's a model object."""
    if hasattr(obj, "to_dict"):
        return obj.to_dict()
    if isinstance(obj, dict):
        return obj
    return {}


def _json_safe(obj: Any) -> Any:
    """Convert date/datetime/decimal to JSON-serializable types for storage."""
    if isinstance(obj, (date, datetime)):
        return obj.isoformat()
    if isinstance(obj, Decimal):
        return str(obj)
    if isinstance(obj, dict):
        return {k: _json_safe(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_json_safe(v) for v in obj]
    return obj


# Environment mapping for Plaid host (SDK has Sandbox and Production only)
PLAID_ENV_MAP = {
    "sandbox": plaid.Environment.Sandbox,
    "development": plaid.Environment.Sandbox,  # Development uses Sandbox in SDK
    "production": plaid.Environment.Production,
}


def _get_plaid_client():
    """Create Plaid API client from Django settings."""
    client_id = getattr(settings, "PLAID_CLIENT_ID", None)
    secret = getattr(settings, "PLAID_SECRET", None)
    env_name = (getattr(settings, "PLAID_ENV", None) or "sandbox").lower()

    if not client_id or not secret:
        raise ValueError("PLAID_CLIENT_ID and PLAID_SECRET must be set")

    host = PLAID_ENV_MAP.get(env_name, plaid.Environment.Sandbox)
    configuration = plaid.Configuration(
        host=host,
        api_key={
            "clientId": client_id,
            "secret": secret,
        },
    )
    api_client = plaid.ApiClient(configuration)
    return plaid_api.PlaidApi(api_client)


def _parse_products() -> list:
    """Parse PLAID_PRODUCTS from settings (comma-separated) into Products list."""
    raw = getattr(settings, "PLAID_PRODUCTS", "transactions") or "transactions"
    parts = [p.strip().lower() for p in raw.split(",") if p.strip()]
    result = []
    for p in parts:
        try:
            result.append(Products(p))
        except (ValueError, AttributeError, TypeError):
            logger.warning("Skipping invalid Plaid product: %s", p)
    return result if result else [Products("transactions")]


def _parse_country_codes() -> list:
    """Parse PLAID_COUNTRY_CODES from settings (comma-separated) into CountryCode list."""
    raw = getattr(settings, "PLAID_COUNTRY_CODES", "US") or "US"
    parts = [c.strip().upper() for c in raw.split(",") if c.strip()]
    return [CountryCode(c) for c in parts]


def create_link_token_for_user(user) -> str:
    """
    Create a Plaid link_token for the given user.
    Frontend uses this to initialize Plaid Link.
    """
    client = _get_plaid_client()
    products = _parse_products()
    country_codes = _parse_country_codes()

    request = LinkTokenCreateRequest(
        products=products,
        client_name=getattr(settings, "PLAID_CLIENT_NAME", "Zapp") or "Zapp",
        country_codes=country_codes,
        language="en",
        user=LinkTokenCreateRequestUser(client_user_id=str(user.pk)),
    )

    webhook_url = (getattr(settings, "PLAID_WEBHOOK_URL", "") or "").strip()
    if webhook_url:
        request.webhook = webhook_url

    redirect_uri = (getattr(settings, "PLAID_REDIRECT_URI", "") or "").strip()
    if redirect_uri:
        request.redirect_uri = redirect_uri

    response = _to_dict(client.link_token_create(request))
    return response.get("link_token", "")


def exchange_public_token_for_user(user, public_token: str) -> BankConnection:
    """
    Exchange public_token for access_token + item_id.
    Creates BankConnection, fetches accounts, runs initial transaction sync.
    """
    client = _get_plaid_client()
    exchange_request = ItemPublicTokenExchangeRequest(public_token=public_token)
    exchange_response = _to_dict(client.item_public_token_exchange(exchange_request))

    access_token = exchange_response.get("access_token", "")
    item_id = exchange_response.get("item_id", "")

    # Get institution info if available (item_public_token_exchange may not include it)
    institution_id = ""
    institution_name = ""

    connection = BankConnection.objects.create(
        user=user,
        plaid_item_id=item_id,
        plaid_access_token=access_token,
        institution_id=institution_id,
        institution_name=institution_name,
        status=BankConnection.Status.ACTIVE,
    )

    fetch_accounts_for_connection(connection)
    sync_transactions_for_connection(connection, cursor=None)

    return connection


def fetch_accounts_for_connection(connection: BankConnection) -> list[BankAccount]:
    """Fetch accounts from Plaid and upsert into DB."""
    client = _get_plaid_client()
    request = AccountsGetRequest(access_token=connection.plaid_access_token)
    response = _to_dict(client.accounts_get(request))
    accounts_data = response.get("accounts", [])

    # Update institution info from item if available
    item = response.get("item") or {}
    if item and (not connection.institution_id or not connection.institution_name):
        connection.institution_id = item.get("institution_id", "") or connection.institution_id
        connection.institution_name = item.get("institution_name", "") or connection.institution_name
        connection.save(update_fields=["institution_id", "institution_name", "updated_at"])

    return upsert_accounts_from_plaid(connection, accounts_data)


def upsert_accounts_from_plaid(
    connection: BankConnection, accounts_payload: list[dict[str, Any]]
) -> list[BankAccount]:
    """Upsert bank accounts from Plaid accounts payload."""
    result = []
    for acc in accounts_payload:
        acc = _to_dict(acc) if not isinstance(acc, dict) else acc
        account_id = acc.get("account_id", "")
        if not account_id:
            continue

        balances = acc.get("balances", {}) or {}
        current = balances.get("current")
        available = balances.get("available")

        account, _ = BankAccount.objects.update_or_create(
            connection=connection,
            plaid_account_id=account_id,
            defaults={
                "name": acc.get("name", ""),
                "official_name": acc.get("official_name", "") or "",
                "mask": acc.get("mask", "") or "",
                "type": acc.get("type", "") or "",
                "subtype": str(acc.get("subtype", "")) if acc.get("subtype") is not None else "",
                "current_balance": _to_decimal(current) if current is not None else None,
                "available_balance": _to_decimal(available) if available is not None else None,
                "iso_currency_code": (
                    (acc.get("balances") or {}).get("iso_currency_code") or "USD"
                ),
                "raw_payload": _json_safe(acc),
            },
        )
        result.append(account)
    return result


def sync_transactions_for_connection(
    connection: BankConnection, cursor: str | None = None
) -> dict:
    """
    Run transactions/sync for the connection.
    Uses connection.sync_cursor if cursor is None.
    Returns dict with added_count, modified_count, removed_count, next_cursor.
    """
    client = _get_plaid_client()
    use_cursor = cursor if cursor is not None else (connection.sync_cursor or "")

    request = TransactionsSyncRequest(
        access_token=connection.plaid_access_token,
        cursor=use_cursor or "",  # Plaid requires str, not None
    )
    response = _to_dict(client.transactions_sync(request))

    added = response.get("added", [])
    modified = response.get("modified", [])
    removed = response.get("removed", [])

    upsert_transactions_from_plaid(connection, {"added": added, "modified": modified, "removed": removed})

    next_cursor = response.get("next_cursor", "")
    has_more = response.get("has_more", False)

    connection.sync_cursor = next_cursor
    connection.last_synced_at = datetime.utcnow()
    connection.save(update_fields=["sync_cursor", "last_synced_at", "updated_at"])

    # Paginate if has_more
    if has_more and next_cursor:
        sub = sync_transactions_for_connection(connection, cursor=next_cursor)
        return {
            "added_count": len(added) + sub.get("added_count", 0),
            "modified_count": len(modified) + sub.get("modified_count", 0),
            "removed_count": len(removed) + sub.get("removed_count", 0),
            "next_cursor": sub.get("next_cursor", next_cursor),
        }

    return {
        "added_count": len(added),
        "modified_count": len(modified),
        "removed_count": len(removed),
        "next_cursor": next_cursor,
    }


def upsert_transactions_from_plaid(
    connection: BankConnection,
    sync_payload: dict[str, list],
) -> None:
    """
    Upsert transactions from transactions/sync response.
    Handles added, modified, and removed.
    """
    account_map = {a.plaid_account_id: a for a in connection.accounts.all()}

    for txn in sync_payload.get("added", []) + sync_payload.get("modified", []):
        txn_dict = _to_dict(txn) if not isinstance(txn, dict) else txn
        _upsert_single_transaction(connection, txn_dict, account_map)

    for removed_item in sync_payload.get("removed", []):
        removed_dict = _to_dict(removed_item) if not isinstance(removed_item, dict) else removed_item
        txn_id = removed_dict.get("transaction_id", "")
        if not txn_id:
            continue
        BankTransaction.objects.filter(
            connection=connection,
            plaid_transaction_id=txn_id,
        ).update(removed=True, updated_at=datetime.utcnow())


def _upsert_single_transaction(
    connection: BankConnection,
    txn: dict,
    account_map: dict[str, BankAccount],
) -> BankTransaction | None:
    """Upsert a single transaction from Plaid payload."""
    txn_id = txn.get("transaction_id", "")
    account_id = txn.get("account_id", "")
    if not txn_id or not account_id:
        return None

    account = account_map.get(account_id)
    if not account:
        return None

    amount = _to_decimal(txn.get("amount", 0))
    # Plaid amounts: positive = money out, negative = money in
    date_val = txn.get("date")
    auth_date_val = txn.get("authorized_date")

    categories = txn.get("category", []) or []
    category_primary = categories[0] if categories else ""
    category_detailed = ", ".join(categories) if isinstance(categories, list) else ""

    parsed_date = _parse_date(date_val)
    if parsed_date is None:
        return None  # Skip transactions without valid date (required column)

    obj, _ = BankTransaction.objects.update_or_create(
        connection=connection,
        plaid_transaction_id=txn_id,
        defaults={
            "user": connection.user,
            "account": account,
            "pending_transaction_id": txn.get("pending_transaction_id", "") or "",
            "name": txn.get("name", ""),
            "merchant_name": txn.get("merchant_name", "") or "",
            "amount": amount,
            "iso_currency_code": txn.get("iso_currency_code", "USD") or "USD",
            "date": parsed_date,
            "authorized_date": _parse_date(auth_date_val),
            "pending": txn.get("pending", False),
            "removed": False,
            "category_primary": category_primary,
            "category_detailed": category_detailed,
            "raw_payload": _json_safe(txn),
        },
    )
    return obj


def _to_decimal(value) -> Decimal:
    if value is None:
        return Decimal("0")
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def _parse_date(val) -> date | None:
    """Parse date from string, or return date/datetime objects as date."""
    if val is None:
        return None
    if isinstance(val, date) and not isinstance(val, datetime):
        return val
    if isinstance(val, datetime):
        return val.date()
    s = str(val).strip()
    if not s:
        return None
    try:
        return date.fromisoformat(s)
    except (ValueError, TypeError):
        return None
