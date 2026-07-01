from __future__ import annotations

import logging
from datetime import timedelta

from django.utils import timezone

from apps.integrations.token_storage import decrypt_oauth_token, encrypt_oauth_token
from apps.integrations.services.spotify_oauth import refresh_access_token

logger = logging.getLogger(__name__)


def refresh_connection_tokens(connection) -> str:
    """Refresh tokens from Spotify and persist; returns plaintext access token."""
    rt = decrypt_oauth_token(connection.refresh_token_encrypted)
    if not rt:
        raise ValueError("missing_refresh_token")

    data = refresh_access_token(refresh_token=rt)
    connection.access_token_encrypted = encrypt_oauth_token(data["access_token"])
    if data.get("refresh_token"):
        connection.refresh_token_encrypted = encrypt_oauth_token(data["refresh_token"])
    expires_in = int(data.get("expires_in") or 3600)
    connection.token_expires_at = timezone.now() + timedelta(seconds=expires_in)
    connection.save(
        update_fields=[
            "access_token_encrypted",
            "refresh_token_encrypted",
            "token_expires_at",
            "updated_at",
        ]
    )
    return decrypt_oauth_token(connection.access_token_encrypted)


def ensure_valid_access_token(connection) -> str:
    """Return a usable access token, refreshing if near expiry."""
    now = timezone.now()
    if (
        connection.token_expires_at
        and connection.token_expires_at > now + timedelta(seconds=60)
        and connection.access_token_encrypted
    ):
        return decrypt_oauth_token(connection.access_token_encrypted)
    return refresh_connection_tokens(connection)
