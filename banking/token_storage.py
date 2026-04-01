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


def _configured_keys() -> list[str]:
    csv_value = getattr(settings, "PLAID_TOKEN_ENCRYPTION_KEYS", None) or ""
    keys = [part.strip() for part in csv_value.split(",") if part.strip()]
    if keys:
        return keys

    single_key = getattr(settings, "PLAID_TOKEN_ENCRYPTION_KEY", None) or ""
    single_key = single_key.strip()
    return [single_key] if single_key else []


def _fernets() -> list[Fernet]:
    if Fernet is None:
        return []

    instances: list[Fernet] = []
    for key in _configured_keys():
        try:
            instances.append(Fernet(key.encode("ascii")))
        except Exception as exc:  # pragma: no cover
            logger.error("Invalid Plaid token encryption key: %s", exc)
    return instances


def _primary_fernet() -> Fernet | None:
    instances = _fernets()
    return instances[0] if instances else None


def has_plaid_token_encryption_enabled() -> bool:
    return _primary_fernet() is not None


def _decrypt_with_fernets(stored: str, fernets: list[Fernet]) -> tuple[str, bool, int | None]:
    for index, f in enumerate(fernets):
        try:
            return f.decrypt(stored.encode("ascii")).decode("utf-8"), True, index
        except (InvalidToken, ValueError, TypeError):
            continue
    # Legacy plaintext row before encryption was enabled
    return stored, False, None


def encrypt_plaid_access_token(plaintext: str) -> str:
    f = _primary_fernet()
    if f is None:
        return plaintext
    return f.encrypt(plaintext.encode("utf-8")).decode("ascii")


def decrypt_plaid_access_token(stored: str) -> str:
    """
    Decrypt stored token, or return as-is if encryption is off or legacy plaintext.
    """
    fernets = _fernets()
    if not fernets:
        return stored
    plaintext, _, _ = _decrypt_with_fernets(stored, fernets)
    return plaintext


def is_plaid_access_token_encrypted(stored: str) -> bool:
    fernets = _fernets()
    if not fernets or not stored:
        return False
    _, was_encrypted, _ = _decrypt_with_fernets(stored, fernets)
    return was_encrypted


def ensure_plaid_access_token_encrypted(connection) -> bool:
    """
    Best-effort upgrade for legacy plaintext rows when encryption is enabled.
    Returns True when the stored value was rewritten as ciphertext.
    """
    raw = getattr(connection, "plaid_access_token", "") or ""
    f = _primary_fernet()
    if f is None or not raw:
        return False

    plaintext, was_encrypted, _ = _decrypt_with_fernets(raw, _fernets())
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
    fernets = _fernets()
    f = fernets[0] if fernets else None
    if f is None:
        return raw

    plaintext, was_encrypted, key_index = _decrypt_with_fernets(raw, fernets)
    if not was_encrypted and plaintext:
        connection.plaid_access_token = f.encrypt(plaintext.encode("utf-8")).decode("ascii")
        if hasattr(connection, "save"):
            connection.save(update_fields=["plaid_access_token", "updated_at"])
        logger.info(
            "plaid_access_token_upgraded_on_access connection_id=%s",
            getattr(connection, "pk", None),
        )
    elif was_encrypted and key_index not in (None, 0):
        # Re-wrap with the current primary key when decrypt succeeds with an older fallback key.
        connection.plaid_access_token = f.encrypt(plaintext.encode("utf-8")).decode("ascii")
        if hasattr(connection, "save"):
            connection.save(update_fields=["plaid_access_token", "updated_at"])
        logger.info(
            "plaid_access_token_rotated_on_access connection_id=%s",
            getattr(connection, "pk", None),
        )
    return plaintext
