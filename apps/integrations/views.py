import logging

from django.conf import settings
from django.http import HttpResponseRedirect
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from apps.integrations.models import SpotifyConnection, SpotifyFeatureSnapshot
from apps.integrations.services.spotify_connection import persist_oauth_tokens_and_profile
from apps.integrations.services.spotify_oauth import (
    build_authorize_url,
    exchange_code_for_tokens,
    parse_state_user_id,
)
from apps.integrations.services.spotify_sync import sync_spotify_for_user
from apps.users.models import User

logger = logging.getLogger(__name__)


def _frontend_redirect(*, ok: bool, message: str = "") -> str:
    base = getattr(settings, "SPOTIFY_OAUTH_SUCCESS_REDIRECT_URL", "") or "/"
    sep = "&" if "?" in base else "?"
    q = f"spotify_connected={'1' if ok else '0'}"
    if message:
        from urllib.parse import quote

        q += f"&spotify_message={quote(message[:500])}"
    return f"{base}{sep}{q}"


class SpotifyConnectView(APIView):
    """Start OAuth: return Spotify authorize URL (Authorization Code flow)."""

    permission_classes = [IsAuthenticated]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "integrations_spotify"

    def post(self, request):
        try:
            url = build_authorize_url(user_id=request.user.pk)
        except ValueError as e:
            return Response({"error": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
        return Response({"auth_url": url})


class SpotifyCallbackView(APIView):
    """
    OAuth redirect handler (browser). Validates signed state, exchanges code, stores tokens.
    """

    permission_classes = [AllowAny]
    authentication_classes = []  # no JWT on browser redirect
    throttle_classes = []  # OAuth redirect should not be throttled as anonymous browser

    def get(self, request):
        err = request.query_params.get("error")
        if err:
            return HttpResponseRedirect(_frontend_redirect(ok=False, message=err))

        code = request.query_params.get("code")
        state = request.query_params.get("state")
        if not code or not state:
            return HttpResponseRedirect(_frontend_redirect(ok=False, message="missing_code_or_state"))

        try:
            user_id = parse_state_user_id(state)
        except ValueError:
            return HttpResponseRedirect(_frontend_redirect(ok=False, message="invalid_state"))

        user = get_object_or_404(User, pk=user_id)

        try:
            tokens = exchange_code_for_tokens(code=code)
            persist_oauth_tokens_and_profile(user, tokens)
        except Exception as exc:
            logger.exception("spotify_oauth_callback_failed user_id=%s", user_id)
            return HttpResponseRedirect(_frontend_redirect(ok=False, message=str(exc)[:200]))

        if getattr(settings, "SPOTIFY_SYNC_ON_CONNECT", True):
            try:
                sync_spotify_for_user(user)
            except Exception:
                logger.exception("spotify_post_oauth_sync_failed user_id=%s", user_id)

        return HttpResponseRedirect(_frontend_redirect(ok=True))


class SpotifyInsightsView(APIView):
    """Latest feature snapshot + connection summary for the subscriptions UI."""

    permission_classes = [IsAuthenticated]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "integrations_spotify"

    def get(self, request):
        conn = SpotifyConnection.objects.filter(user=request.user).first()
        if not conn:
            return Response(
                {
                    "connected": False,
                    "sync_status": None,
                    "last_synced_at": None,
                    "last_error": None,
                    "latest_snapshot": None,
                }
            )
        snap = (
            SpotifyFeatureSnapshot.objects.filter(user=request.user)
            .order_by("-computed_at")
            .select_related("subscription")
            .first()
        )
        payload = {
            "connected": bool(conn.refresh_token_encrypted),
            "sync_status": conn.sync_status,
            "last_synced_at": conn.last_synced_at,
            "last_error": conn.last_error,
            "display_name": conn.display_name or None,
            "spotify_user_id": conn.spotify_user_id or None,
            "product": conn.product or None,
            "latest_snapshot": None,
        }
        if snap:
            payload["latest_snapshot"] = {
                "id": snap.pk,
                "computed_at": snap.computed_at,
                "feature_version": snap.feature_version,
                "features": snap.features,
            }
        return Response(payload)


class SpotifyStatusView(APIView):
    permission_classes = [IsAuthenticated]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "integrations_spotify"

    def get(self, request):
        conn = SpotifyConnection.objects.filter(user=request.user).first()
        if not conn:
            return Response(
                {
                    "connected": False,
                    "sync_status": None,
                    "last_synced_at": None,
                    "display_name": None,
                    "spotify_user_id": None,
                    "product": None,
                }
            )
        return Response(
            {
                "connected": bool(conn.refresh_token_encrypted),
                "sync_status": conn.sync_status,
                "last_synced_at": conn.last_synced_at,
                "last_error": conn.last_error,
                "display_name": conn.display_name or None,
                "spotify_user_id": conn.spotify_user_id or None,
                "product": conn.product or None,
            }
        )


class SpotifySyncView(APIView):
    permission_classes = [IsAuthenticated]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "integrations_spotify"

    def post(self, request):
        try:
            snap = sync_spotify_for_user(request.user)
        except ValueError as e:
            if str(e) == "spotify_not_connected":
                return Response({"error": "not_connected"}, status=status.HTTP_400_BAD_REQUEST)
            if str(e) == "spotify_missing_refresh_token":
                return Response({"error": "missing_refresh_token"}, status=status.HTTP_400_BAD_REQUEST)
            raise
        except Exception:
            logger.exception("spotify_sync_view_failed user_id=%s", request.user.pk)
            return Response({"error": "sync_failed"}, status=status.HTTP_502_BAD_GATEWAY)

        return Response(
            {
                "ok": True,
                "snapshot_id": snap.pk,
                "computed_at": snap.computed_at,
                "features": snap.features,
            }
        )


class SpotifyDisconnectView(APIView):
    permission_classes = [IsAuthenticated]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "integrations_spotify"

    def delete(self, request):
        deleted, _ = SpotifyConnection.objects.filter(user=request.user).delete()
        return Response({"disconnected": bool(deleted)})
