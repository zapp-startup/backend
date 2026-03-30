"""
Plaid access token storage helpers.

App-layer encryption (Fernet) is optional via PLAID_TOKEN_ENCRYPTION_KEY.
When unset, tokens are stored as received (rely on database TLS + provider encryption at rest).

For KMS/Vault: replace encrypt_value/decrypt_value with calls to your provider
and keep the same public API used by plaid_service.
"""
from __future__ import annotations

import logging

from django.conf import settings

logger = logging.getLogger(__name__)

try:
    from cryptography.fernet import Fernet, InvalidToken
except ImportError:  # pragma: no cover
    Fernet = None  # type: ignore[misc, assignment]
    InvalidToken = Exception  # type: ignore[misc, assignment]


def _fernet() -> Fernet | None:
    key = getattr(settings, "PLAID_TOKEN_ENCRYPTION_KEY", None) or ""
    key = key.strip()
    if not key or Fernet is None:
        return None
    try:
        return Fernet(key.encode("ascii"))
    except Exception as exc:  # pragma: no cover
        logger.error("Invalid PLAID_TOKEN_ENCRYPTION_KEY: %s", exc)
        return None


def encrypt_plaid_access_token(plaintext: str) -> str:
    f = _fernet()
    if f is None:
        return plaintext
    return f.encrypt(plaintext.encode("utf-8")).decode("ascii")


def decrypt_plaid_access_token(stored: str) -> str:
    """
    Decrypt stored token, or return as-is if encryption is off or legacy plaintext.
    """
    f = _fernet()
    if f is None:
        return stored
    try:
        return f.decrypt(stored.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError, TypeError):
        # Legacy plaintext row before encryption was enabled
        return stored


def get_plaid_access_token_for_api(connection) -> str:
    """Use for all Plaid API calls — never expose return value to clients."""
    raw = connection.plaid_access_token
    return decrypt_plaid_access_token(raw)
