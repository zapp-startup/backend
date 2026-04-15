from __future__ import annotations

import logging
from decimal import Decimal

from django.conf import settings
from django.utils import timezone

from integrations.models import (
    SpotifyConnection,
    SpotifyFeatureSnapshot,
    SpotifyPayloadType,
    SpotifyPlayEvent,
    SpotifyRawPayload,
    SpotifySyncRun,
    SpotifySyncStatus,
)
from integrations.services import spotify_api
from integrations.services.spotify_features import compute_engineered_features, rollup_to_subscription_fields
from integrations.services.spotify_subscription import (
    apply_spotify_rollups,
    resolve_spotify_subscription,
)

logger = logging.getLogger(__name__)


def _store_payload(sync_run, user, ptype: str, data: dict | list | None) -> None:
    if data is None:
        return
    SpotifyRawPayload.objects.create(
        user=user,
        sync_run=sync_run,
        payload_type=ptype,
        raw_json=data if isinstance(data, (dict, list)) else {"value": data},
    )


def _ingest_recent_plays(sync_run, user, recently_played: dict | None) -> None:
    items = (recently_played or {}).get("items") or []
    for row in items:
        track = row.get("track") or {}
        if not isinstance(track, dict):
            continue
        played_at_raw = row.get("played_at")
        if not played_at_raw:
            continue
        from integrations.services.spotify_features import parse_spotify_timestamp

        played_at = parse_spotify_timestamp(played_at_raw)
        if not played_at:
            continue
        tid = track.get("id") or ""
        if not tid:
            continue
        name = (track.get("name") or "")[:512]
        artists = track.get("artists") or []
        an = ""
        if artists and isinstance(artists[0], dict):
            an = (artists[0].get("name") or "")[:512]
        uri = (track.get("uri") or "")[:128]
        SpotifyPlayEvent.objects.get_or_create(
            user=user,
            track_id=tid,
            played_at=played_at,
            defaults={
                "sync_run": sync_run,
                "track_uri": uri,
                "track_name": name,
                "artist_name": an,
            },
        )


def sync_spotify_for_user(user) -> SpotifyFeatureSnapshot:
    """
    Full read-only sync + feature snapshot + subscription rollups.
    """
    try:
        connection = SpotifyConnection.objects.get(user=user)
    except SpotifyConnection.DoesNotExist as exc:
        raise ValueError("spotify_not_connected") from exc

    if not connection.refresh_token_encrypted:
        raise ValueError("spotify_missing_refresh_token")

    connection.sync_status = SpotifySyncStatus.SYNCING
    connection.save(update_fields=["sync_status", "updated_at"])

    run = SpotifySyncRun.objects.create(
        user=user,
        connection=connection,
        status=SpotifySyncStatus.PENDING,
    )

    try:
        profile = spotify_api.fetch_profile(connection)
        _store_payload(run, user, SpotifyPayloadType.PROFILE, profile or {})

        recently_played = spotify_api.fetch_recently_played(connection, limit=50)
        _store_payload(run, user, SpotifyPayloadType.RECENTLY_PLAYED, recently_played or {})

        player = spotify_api.fetch_player(connection)
        _store_payload(run, user, SpotifyPayloadType.PLAYER, player or {})

        current = spotify_api.fetch_currently_playing(connection)
        _store_payload(run, user, SpotifyPayloadType.CURRENTLY_PLAYING, current or {})

        top_tracks = spotify_api.fetch_top_tracks(connection, limit=50)
        _store_payload(run, user, SpotifyPayloadType.TOP_TRACKS, top_tracks or {})

        top_artists = spotify_api.fetch_top_artists(connection, limit=50)
        _store_payload(run, user, SpotifyPayloadType.TOP_ARTISTS, top_artists or {})

        saved_total = spotify_api.fetch_saved_tracks_total(connection)
        _store_payload(
            run,
            user,
            SpotifyPayloadType.SAVED_TRACKS,
            {"total": saved_total, "note": "count from /me/tracks total field"},
        )

        _ingest_recent_plays(run, user, recently_played)

        sub = resolve_spotify_subscription(user)
        price = float(
            sub.price
            if sub is not None
            else Decimal(str(getattr(settings, "SPOTIFY_DEFAULT_MONTHLY_PRICE_USD", "10.99")))
        )

        now = timezone.now()
        feature_payload = compute_engineered_features(
            now=now,
            recently_played=recently_played,
            top_tracks=top_tracks,
            top_artists=top_artists,
            saved_tracks_total=saved_total,
            currently_playing=current,
            player=player,
            monthly_price=price,
        )

        rollup = rollup_to_subscription_fields(feature_payload)
        feature_payload["subscription_rollup"] = rollup

        snap = SpotifyFeatureSnapshot.objects.create(
            user=user,
            subscription=sub,
            sync_run=run,
            feature_version=SpotifyFeatureSnapshot.FEATURE_VERSION,
            features=feature_payload,
        )

        apply_spotify_rollups(sub, rollup)

        connection.last_synced_at = now
        connection.sync_status = SpotifySyncStatus.OK
        connection.last_error = None
        connection.save(
            update_fields=["last_synced_at", "sync_status", "last_error", "updated_at"]
        )

        run.status = SpotifySyncStatus.OK
        run.finished_at = now
        run.save(update_fields=["status", "finished_at"])

        return snap

    except Exception as exc:
        logger.exception("spotify_sync_failed user_id=%s", user.pk)
        now = timezone.now()
        connection.sync_status = SpotifySyncStatus.ERROR
        connection.last_error = str(exc)[:2000]
        connection.save(update_fields=["sync_status", "last_error", "updated_at"])
        run.status = SpotifySyncStatus.ERROR
        run.error_message = str(exc)[:2000]
        run.finished_at = now
        run.save(update_fields=["status", "error_message", "finished_at"])
        raise
