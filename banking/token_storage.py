"""
Plaid access token storage helpers.

App-layer encryption (Fernet) is enabled via PLAID_TOKEN_ENCRYPTION_KEY.
In production, the key is required by startup validation. In development, plaintext
storage remains possible for local setup convenience.

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


def has_plaid_token_encryption_enabled() -> bool:
    return _fernet() is not None


def _decrypt_with_fernet(stored: str, f: Fernet) -> tuple[str, bool]:
    try:
        return f.decrypt(stored.encode("ascii")).decode("utf-8"), True
    except (InvalidToken, ValueError, TypeError):
        # Legacy plaintext row before encryption was enabled
        return stored, False


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
    plaintext, _ = _decrypt_with_fernet(stored, f)
    return plaintext


def is_plaid_access_token_encrypted(stored: str) -> bool:
    f = _fernet()
    if f is None or not stored:
        return False
    _, was_encrypted = _decrypt_with_fernet(stored, f)
    return was_encrypted


def ensure_plaid_access_token_encrypted(connection) -> bool:
    """
    Best-effort upgrade for legacy plaintext rows when encryption is enabled.
    Returns True when the stored value was rewritten as ciphertext.
    """
    raw = getattr(connection, "plaid_access_token", "") or ""
    f = _fernet()
    if f is None or not raw:
        return False

    plaintext, was_encrypted = _decrypt_with_fernet(raw, f)
    if was_encrypted:
        return False

    connection.plaid_access_token = f.encrypt(plaintext.encode("utf-8")).decode("ascii")
    if hasattr(connection, "save"):
        connection.save(update_fields=["plaid_access_token", "updated_at"])
    logger.info(
        "plaid_access_token_upgraded connection_id=%s",
        getattr(connection, "pk", None),
    )
    return True


def get_plaid_access_token_for_api(connection) -> str:
    """Use for all Plaid API calls — never expose return value to clients."""
    raw = connection.plaid_access_token
    f = _fernet()
    if f is None:
        return raw

    plaintext, was_encrypted = _decrypt_with_fernet(raw, f)
    if not was_encrypted and plaintext:
        connection.plaid_access_token = f.encrypt(plaintext.encode("utf-8")).decode("ascii")
        if hasattr(connection, "save"):
            connection.save(update_fields=["plaid_access_token", "updated_at"])
        logger.info(
            "plaid_access_token_upgraded_on_access connection_id=%s",
            getattr(connection, "pk", None),
        )
    return plaintext
