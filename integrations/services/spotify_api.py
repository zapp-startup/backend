from __future__ import annotations

import logging

import requests

from integrations.models import SpotifyConnection
from integrations.services.token_refresh import ensure_valid_access_token, refresh_connection_tokens

logger = logging.getLogger(__name__)


def _headers(access_token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {access_token}"}


def _abs_url(path: str) -> str:
    if path.startswith("http"):
        return path
    base = "https://api.spotify.com/v1"
    if not path.startswith("/"):
        path = "/" + path
    return base + path


def _get(
    connection: SpotifyConnection,
    path: str,
    *,
    params: dict | None = None,
) -> tuple[dict | list | None, int]:
    url = _abs_url(path)
    token = ensure_valid_access_token(connection)

    def do_request(tok: str) -> requests.Response:
        return requests.get(
            url,
            headers=_headers(tok),
            params=params or {},
            timeout=30,
        )

    r = do_request(token)
    if r.status_code == 401:
        token = refresh_connection_tokens(connection)
        r = do_request(token)

    if r.status_code == 204:
        return None, r.status_code
    if r.status_code in (401, 403, 404):
        return None, r.status_code
    if r.status_code != 200:
        logger.warning("spotify_api_get path=%s status=%s body=%s", path, r.status_code, r.text[:400])
        return None, r.status_code
    try:
        return r.json(), r.status_code
    except ValueError:
        return None, r.status_code


def fetch_profile(connection: SpotifyConnection) -> dict | None:
    data, _ = _get(connection, "/me")
    return data if isinstance(data, dict) else None


def fetch_recently_played(connection: SpotifyConnection, *, limit: int = 50) -> dict | None:
    data, _ = _get(connection, "/me/player/recently-played", params={"limit": limit})
    return data if isinstance(data, dict) else None


def fetch_player(connection: SpotifyConnection) -> dict | None:
    data, status = _get(connection, "/me/player")
    if status == 204:
        return None
    return data if isinstance(data, dict) else None


def fetch_currently_playing(connection: SpotifyConnection) -> dict | None:
    data, status = _get(connection, "/me/player/currently-playing")
    if status == 204:
        return None
    return data if isinstance(data, dict) else None


def fetch_top_tracks(connection: SpotifyConnection, *, limit: int = 50) -> dict | None:
    data, _ = _get(
        connection,
        "/me/top/tracks",
        params={"time_range": "short_term", "limit": limit},
    )
    return data if isinstance(data, dict) else None


def fetch_top_artists(connection: SpotifyConnection, *, limit: int = 50) -> dict | None:
    data, _ = _get(
        connection,
        "/me/top/artists",
        params={"time_range": "short_term", "limit": limit},
    )
    return data if isinstance(data, dict) else None


def fetch_saved_tracks_page(
    connection: SpotifyConnection,
    *,
    limit: int = 50,
    offset: int = 0,
) -> dict | None:
    data, _ = _get(
        connection,
        "/me/tracks",
        params={"limit": limit, "offset": offset},
    )
    return data if isinstance(data, dict) else None


def fetch_saved_tracks_total(connection: SpotifyConnection) -> int:
    page = fetch_saved_tracks_page(connection, limit=1, offset=0)
    if not page:
        return 0
    return int(page.get("total") or 0)
