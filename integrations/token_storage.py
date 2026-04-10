"""
Spotify OAuth token storage — same Fernet pattern as banking (optional at-rest encryption).
Uses SPOTIFY_TOKEN_ENCRYPTION_KEY if set, else PLAID_TOKEN_ENCRYPTION_KEY, else plaintext.
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
    key = (
        getattr(settings, "SPOTIFY_TOKEN_ENCRYPTION_KEY", None)
        or getattr(settings, "PLAID_TOKEN_ENCRYPTION_KEY", None)
        or ""
    )
    key = (key or "").strip()
    if not key or Fernet is None:
        return None
    try:
        return Fernet(key.encode("ascii"))
    except Exception as exc:  # pragma: no cover
        logger.error("Invalid OAuth token encryption key: %s", exc)
        return None


def encrypt_oauth_token(plaintext: str) -> str:
    f = _fernet()
    if f is None:
        return plaintext
    return f.encrypt(plaintext.encode("utf-8")).decode("ascii")


def decrypt_oauth_token(stored: str) -> str:
    f = _fernet()
    if f is None:
        return stored
    try:
        return f.decrypt(stored.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError, TypeError):
        return stored
