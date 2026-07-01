from __future__ import annotations

import logging
from datetime import timedelta

from django.utils import timezone

from apps.integrations.constants import SPOTIFY_SCOPES
from apps.integrations.models import SpotifyConnection, SpotifySyncStatus
from apps.integrations.token_storage import encrypt_oauth_token
from apps.integrations.services import spotify_api

logger = logging.getLogger(__name__)


def get_or_create_connection(user) -> SpotifyConnection:
    conn, _ = SpotifyConnection.objects.get_or_create(user=user)
    return conn


def persist_oauth_tokens_and_profile(user, token_payload: dict) -> SpotifyConnection:
    """Store tokens from authorization code exchange and hydrate /me profile."""
    access = token_payload["access_token"]
    refresh = token_payload.get("refresh_token") or ""
    expires_in = int(token_payload.get("expires_in") or 3600)

    connection = get_or_create_connection(user)
    connection.access_token_encrypted = encrypt_oauth_token(access)
    if refresh:
        connection.refresh_token_encrypted = encrypt_oauth_token(refresh)
    connection.token_expires_at = timezone.now() + timedelta(seconds=expires_in)
    scope = token_payload.get("scope") or SPOTIFY_SCOPES
    connection.granted_scopes = scope.replace(",", " ") if isinstance(scope, str) else SPOTIFY_SCOPES
    connection.sync_status = SpotifySyncStatus.IDLE
    connection.last_error = None
    connection.save()

    profile = spotify_api.fetch_profile(connection)
    if profile:
        connection.spotify_user_id = profile.get("id") or ""
        connection.spotify_uri = profile.get("uri") or ""
        connection.display_name = (profile.get("display_name") or "")[:255]
        email = profile.get("email")
        connection.email = email if email else None
        connection.country = (profile.get("country") or "")[:8]
        connection.product = (profile.get("product") or "")[:32]
        connection.raw_profile_json = profile
        connection.save(
            update_fields=[
                "spotify_user_id",
                "spotify_uri",
                "display_name",
                "email",
                "country",
                "product",
                "raw_profile_json",
                "updated_at",
            ]
        )
    return connection
