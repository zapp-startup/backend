from __future__ import annotations

from datetime import timedelta
from typing import Any

from django.utils import timezone

from integrations.constants import CANONICAL_CATEGORY


def parse_spotify_timestamp(iso: str | None):
    if not iso:
        return None
    try:
        from django.utils.dateparse import parse_datetime

        dt = parse_datetime(iso)
        if dt is None:
            return None
        if timezone.is_naive(dt):
            dt = timezone.make_aware(dt, timezone.utc)
        return dt
    except Exception:
        return None


def _hhi_from_items(items: list[dict], *, key: str = "popularity") -> float:
    """Concentration in [0,1] using normalized popularity weights (Herfindahl)."""
    if not items:
        return 0.0
    weights = [float((it.get(key) or 0) + 1) for it in items]
    s = sum(weights)
    if s <= 0:
        n = len(items)
        shares = [1.0 / n] * n
    else:
        shares = [w / s for w in weights]
    return float(sum(x * x for x in shares))


def compute_engineered_features(
    *,
    now,
    recently_played: dict | None,
    top_tracks: dict | None,
    top_artists: dict | None,
    saved_tracks_total: int,
    currently_playing: dict | None,
    player: dict | None,
    monthly_price: float,
) -> dict[str, Any]:
    """
    Proxy features — Spotify does not expose total listening minutes.
    Assumptions documented in snapshot under `assumptions`.
    """
    window_end = now
    window_start = now - timedelta(days=30)

    plays_in_window: list[tuple[Any, str, str, str]] = []
    # (played_at, track_id, track_name, artist_name)

    items = (recently_played or {}).get("items") or []
    for row in items:
        played_at = parse_spotify_timestamp(row.get("played_at"))
        track = (row.get("track") or {}) if isinstance(row.get("track"), dict) else {}
        tid = track.get("id") or ""
        name = track.get("name") or ""
        artists = track.get("artists") or []
        an = ""
        if artists and isinstance(artists[0], dict):
            an = artists[0].get("name") or ""
        if played_at and window_start <= played_at <= window_end:
            plays_in_window.append((played_at, tid, name, an))

    days_set = {p[0].date() for p in plays_in_window}
    days_used_last_30 = len(days_set)
    tracks_played_last_30 = len(plays_in_window)

    active_listening_days = days_used_last_30

    seven_ago = now - timedelta(days=7)
    plays_last_7 = [p for p in plays_in_window if p[0] >= seven_ago]
    plays_8_30 = [p for p in plays_in_window if p[0] < seven_ago]
    n7 = len(plays_last_7)
    n23 = len(plays_8_30)
    total30 = n7 + n23
    recent_usage_decay = 0.0
    if total30 > 0:
        recent_usage_decay = 1.0 - (n7 / total30)

    tt_items = (top_tracks or {}).get("items") or []
    ta_items = (top_artists or {}).get("items") or []
    top_track_concentration = _hhi_from_items(tt_items if isinstance(tt_items, list) else [])
    top_artist_concentration = _hhi_from_items(ta_items if isinstance(ta_items, list) else [])

    library_engagement_score = min(1.0, float(saved_tracks_total) / 500.0)

    current_playback_seen_recently = bool(
        currently_playing and currently_playing.get("item")
    ) or bool(player and player.get("is_playing") and player.get("item"))

    est_cost = float(monthly_price) / max(1, days_used_last_30)
    estimated_cost_per_active_day = est_cost

    return {
        "canonical_category": CANONICAL_CATEGORY,
        "days_used_last_30": days_used_last_30,
        "tracks_played_last_30": tracks_played_last_30,
        "active_listening_days": active_listening_days,
        "recent_usage_decay": round(recent_usage_decay, 6),
        "top_artist_concentration": round(top_artist_concentration, 6),
        "top_track_concentration": round(top_track_concentration, 6),
        "library_engagement_score": round(library_engagement_score, 6),
        "current_playback_seen_recently": current_playback_seen_recently,
        "estimated_cost_per_active_day": round(estimated_cost_per_active_day, 6),
        "saved_tracks_total": int(saved_tracks_total),
        "assumptions": [
            "Listening time uses recently-played history only (max ~50 recent plays per request); not total monthly minutes.",
            "active_listening_days equals distinct days with at least one captured play in the last 30 days from that history.",
            "Top concentration uses short_term top items with popularity-weighted Herfindahl.",
            "Default monthly price may be estimated when creating a Subscription row.",
        ],
    }


def rollup_to_subscription_fields(features: dict) -> dict[str, float]:
    """
    Map engineered features into Subscription columns (non-breaking, bounded).
    """
    days = int(features.get("days_used_last_30") or 0)
    tracks = int(features.get("tracks_played_last_30") or 0)
    lib = float(features.get("library_engagement_score") or 0.0)
    decay = float(features.get("recent_usage_decay") or 0.0)
    cost_day = float(features.get("estimated_cost_per_active_day") or 0.0)

    # 0-1: share of last 30 days with at least one captured play (proxy; not total minutes).
    usage_frequency = round(min(1.0, max(0.0, float(days) / 30.0)), 6)

    # Utilization: blend activity density + library + light use of track volume
    activity = min(1.0, days / 30.0)
    track_density = min(1.0, tracks / 30.0)
    subscription_utilization = max(
        0.0,
        min(
            1.0,
            0.45 * activity + 0.35 * lib + 0.20 * track_density,
        ),
    )

    # Cost-benefit: lower $/active day is better; cap curve at 2.0 $/day -> 0 score
    subscription_cost_benefit = max(0.0, min(1.0, 1.0 - min(1.0, cost_day / 2.0)))

    # Confidence: stronger when we saw more distinct days (Spotify-backed)
    feedback_confidence = max(0.0, min(1.0, 0.35 + 0.45 * activity + 0.15 * (1.0 - decay)))

    return {
        "usage_frequency": float(usage_frequency),
        "subscription_utilization": round(subscription_utilization, 6),
        "subscription_cost_benefit": round(subscription_cost_benefit, 6),
        "feedback_confidence": round(feedback_confidence, 6),
    }
