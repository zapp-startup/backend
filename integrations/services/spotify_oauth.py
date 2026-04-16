from __future__ import annotations

import logging
from urllib.parse import urlencode

import requests
from django.conf import settings
from django.core.signing import BadSignature, SignatureExpired, TimestampSigner

from integrations.constants import SPOTIFY_AUTH_BASE, SPOTIFY_SCOPES

logger = logging.getLogger(__name__)

_signer = TimestampSigner(salt="integrations.spotify.oauth")


def build_authorize_url(*, user_id: int) -> str:
    client_id = getattr(settings, "SPOTIFY_CLIENT_ID", None) or ""
    redirect_uri = getattr(settings, "SPOTIFY_REDIRECT_URI", None) or ""
    if not client_id or not redirect_uri:
        raise ValueError("SPOTIFY_CLIENT_ID and SPOTIFY_REDIRECT_URI must be configured")

    state = _signer.sign(str(user_id))
    params = {
        "client_id": client_id,
        "response_type": "code",
        "redirect_uri": redirect_uri,
        "scope": SPOTIFY_SCOPES,
        "state": state,
        "show_dialog": "true",
    }
    return f"{SPOTIFY_AUTH_BASE}/authorize?{urlencode(params)}"


def parse_state_user_id(state: str, *, max_age_seconds: int = 600) -> int:
    try:
        uid = _signer.unsign(state, max_age=max_age_seconds)
        return int(uid)
    except (BadSignature, SignatureExpired, ValueError) as exc:
        raise ValueError("invalid_state") from exc


def exchange_code_for_tokens(*, code: str) -> dict:
    client_id = getattr(settings, "SPOTIFY_CLIENT_ID", None) or ""
    client_secret = getattr(settings, "SPOTIFY_CLIENT_SECRET", None) or ""
    redirect_uri = getattr(settings, "SPOTIFY_REDIRECT_URI", None) or ""
    if not client_id or not client_secret or not redirect_uri:
        raise ValueError("Spotify OAuth is not fully configured")

    resp = requests.post(
        f"{SPOTIFY_AUTH_BASE}/api/token",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "client_id": client_id,
            "client_secret": client_secret,
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=30,
    )
    if resp.status_code != 200:
        logger.warning("spotify_token_exchange_failed status=%s body=%s", resp.status_code, resp.text[:500])
        resp.raise_for_status()
    return resp.json()


def refresh_access_token(*, refresh_token: str) -> dict:
    client_id = getattr(settings, "SPOTIFY_CLIENT_ID", None) or ""
    client_secret = getattr(settings, "SPOTIFY_CLIENT_SECRET", None) or ""
    if not client_id or not client_secret:
        raise ValueError("Spotify OAuth is not fully configured")

    resp = requests.post(
        f"{SPOTIFY_AUTH_BASE}/api/token",
        data={
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": client_id,
            "client_secret": client_secret,
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=30,
    )
    if resp.status_code != 200:
        logger.warning("spotify_refresh_failed status=%s body=%s", resp.status_code, resp.text[:500])
        resp.raise_for_status()
    return resp.json()
