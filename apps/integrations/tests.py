from datetime import timedelta
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from apps.integrations.models import SpotifyConnection, SpotifySyncStatus
from apps.integrations.services.spotify_features import compute_engineered_features, rollup_to_subscription_fields
from apps.integrations.services.spotify_oauth import build_authorize_url, parse_state_user_id
from apps.integrations.services.token_refresh import ensure_valid_access_token, refresh_connection_tokens
from apps.integrations.token_storage import decrypt_oauth_token, encrypt_oauth_token
from apps.subscriptions.models import (
    Merchant,
    MerchantCategory,
    Subscription,
    SubscriptionStatus,
)
from apps.users.models import User


class SpotifyOAuthTests(TestCase):
    @override_settings(
        SPOTIFY_CLIENT_ID="cid",
        SPOTIFY_REDIRECT_URI="http://127.0.0.1:8000/api/integrations/spotify/callback/",
    )
    def test_build_authorize_url_contains_client_and_state(self):
        url = build_authorize_url(user_id=42)
        self.assertIn("accounts.spotify.com/authorize", url)
        self.assertIn("client_id=cid", url)
        self.assertIn("state=", url)

    @override_settings(
        SPOTIFY_CLIENT_ID="cid",
        SPOTIFY_REDIRECT_URI="http://127.0.0.1:8000/api/integrations/spotify/callback/",
    )
    def test_parse_state_roundtrip(self):
        url = build_authorize_url(user_id=7)
        from urllib.parse import parse_qs, urlparse

        q = parse_qs(urlparse(url).query)
        state = q["state"][0]
        self.assertEqual(parse_state_user_id(state), 7)


class SpotifyTokenStorageTests(TestCase):
    def test_encrypt_decrypt_roundtrip(self):
        raw = "secret-token"
        enc = encrypt_oauth_token(raw)
        self.assertEqual(decrypt_oauth_token(enc), raw)


@override_settings(
    SPOTIFY_CLIENT_ID="x",
    SPOTIFY_CLIENT_SECRET="y",
    SPOTIFY_REDIRECT_URI="http://127.0.0.1/cb",
)
class SpotifyTokenRefreshTests(TestCase):
    @patch("apps.integrations.services.token_refresh.refresh_access_token")
    def test_refresh_connection_tokens_updates_expiry(self, mock_refresh):
        mock_refresh.return_value = {
            "access_token": "new_access",
            "expires_in": 3600,
        }
        user = User.objects.create_user(username="t1", email="t1@test.com", password="p")
        conn = SpotifyConnection.objects.create(
            user=user,
            refresh_token_encrypted=encrypt_oauth_token("old_refresh"),
        )
        refresh_connection_tokens(conn)
        conn.refresh_from_db()
        self.assertEqual(decrypt_oauth_token(conn.access_token_encrypted), "new_access")
        self.assertIsNotNone(conn.token_expires_at)

    @patch("apps.integrations.services.token_refresh.refresh_connection_tokens")
    def test_ensure_valid_uses_cached_when_fresh(self, mock_refresh):
        user = User.objects.create_user(username="t2", email="t2@test.com", password="p")
        conn = SpotifyConnection.objects.create(
            user=user,
            access_token_encrypted=encrypt_oauth_token("acc"),
            refresh_token_encrypted=encrypt_oauth_token("ref"),
            token_expires_at=timezone.now() + timedelta(hours=2),
        )
        tok = ensure_valid_access_token(conn)
        self.assertEqual(tok, "acc")
        mock_refresh.assert_not_called()


class SpotifyFeatureComputationTests(TestCase):
    def test_compute_and_rollup(self):
        now = timezone.now()
        recently_played = {
            "items": [
                {
                    "played_at": (now - timedelta(days=1)).isoformat(),
                    "track": {
                        "id": "a",
                        "name": "T",
                        "popularity": 10,
                        "artists": [{"name": "Ar"}],
                    },
                }
            ]
        }
        top_tracks = {"items": [{"popularity": 50}, {"popularity": 50}]}
        top_artists = {"items": [{"popularity": 80}, {"popularity": 20}]}
        feats = compute_engineered_features(
            now=now,
            recently_played=recently_played,
            top_tracks=top_tracks,
            top_artists=top_artists,
            saved_tracks_total=100,
            currently_playing=None,
            player=None,
            monthly_price=10.0,
        )
        self.assertGreaterEqual(feats["days_used_last_30"], 1)
        rollup = rollup_to_subscription_fields(feats)
        self.assertIn("usage_frequency", rollup)
        self.assertLessEqual(rollup["subscription_utilization"], 1.0)


class SpotifyAPITests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(
            username="api_spotify",
            email="api@test.com",
            password="testpass123",
        )

    def test_connect_requires_auth(self):
        res = self.client.post("/api/integrations/spotify/connect/")
        self.assertIn(res.status_code, (401, 403))

    @override_settings(
        SPOTIFY_CLIENT_ID="cid",
        SPOTIFY_CLIENT_SECRET="sec",
        SPOTIFY_REDIRECT_URI="http://127.0.0.1:8000/api/integrations/spotify/callback/",
    )
    def test_connect_returns_auth_url(self):
        self.client.force_authenticate(
            user=self.user,
            token={"aal": "aal1", "mfa_factors_count": 0, "amr": []},
        )
        res = self.client.post("/api/integrations/spotify/connect/")
        self.assertEqual(res.status_code, 200)
        self.assertIn("auth_url", res.json())

    @override_settings(
        SPOTIFY_CLIENT_ID="cid",
        SPOTIFY_CLIENT_SECRET="sec",
        SPOTIFY_REDIRECT_URI="http://127.0.0.1:8000/api/integrations/spotify/callback/",
        SPOTIFY_SYNC_ON_CONNECT=False,
    )
    @patch("apps.integrations.views.exchange_code_for_tokens")
    @patch("apps.integrations.views.persist_oauth_tokens_and_profile")
    def test_callback_exchanges_code(self, mock_persist, mock_exchange):
        mock_exchange.return_value = {
            "access_token": "a",
            "refresh_token": "r",
            "expires_in": 3600,
        }
        from urllib.parse import parse_qs, urlparse

        from apps.integrations.services.spotify_oauth import build_authorize_url

        auth_url = build_authorize_url(user_id=self.user.pk)
        state = parse_qs(urlparse(auth_url).query)["state"][0]
        res = self.client.get(
            "/api/integrations/spotify/callback/",
            {"code": "authcode", "state": state},
        )
        self.assertEqual(res.status_code, 302)
        mock_persist.assert_called_once()

    def test_insights_not_connected(self):
        self.client.force_authenticate(
            user=self.user,
            token={"aal": "aal1", "mfa_factors_count": 0, "amr": []},
        )
        res = self.client.get("/api/integrations/spotify/insights/")
        self.assertEqual(res.status_code, 200)
        self.assertFalse(res.json()["connected"])
        self.assertIsNone(res.json()["latest_snapshot"])

    def test_status_not_connected(self):
        self.client.force_authenticate(
            user=self.user,
            token={"aal": "aal1", "mfa_factors_count": 0, "amr": []},
        )
        res = self.client.get("/api/integrations/spotify/status/")
        self.assertEqual(res.status_code, 200)
        self.assertFalse(res.json()["connected"])

    @patch("apps.integrations.views.sync_spotify_for_user")
    def test_sync_requires_connection(self, mock_sync):
        mock_sync.side_effect = ValueError("spotify_not_connected")
        self.client.force_authenticate(
            user=self.user,
            token={"aal": "aal1", "mfa_factors_count": 0, "amr": []},
        )
        res = self.client.post("/api/integrations/spotify/sync/")
        self.assertEqual(res.status_code, 400)

    def test_disconnect_idempotent(self):
        self.client.force_authenticate(
            user=self.user,
            token={"aal": "aal1", "mfa_factors_count": 0, "amr": []},
        )
        res = self.client.delete("/api/integrations/spotify/disconnect/")
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.json()["disconnected"] in (True, False))


class SpotifySubscriptionLinkageTests(TestCase):
    @patch("apps.integrations.services.spotify_sync.spotify_api.fetch_profile")
    @patch("apps.integrations.services.spotify_sync.spotify_api.fetch_recently_played")
    @patch("apps.integrations.services.spotify_sync.spotify_api.fetch_player")
    @patch("apps.integrations.services.spotify_sync.spotify_api.fetch_currently_playing")
    @patch("apps.integrations.services.spotify_sync.spotify_api.fetch_top_tracks")
    @patch("apps.integrations.services.spotify_sync.spotify_api.fetch_top_artists")
    @patch("apps.integrations.services.spotify_sync.spotify_api.fetch_saved_tracks_total")
    def test_sync_updates_subscription_rollups(
        self,
        mock_saved,
        mock_ta,
        mock_tt,
        mock_cp,
        mock_pl,
        mock_rp,
        mock_prof,
    ):
        mock_prof.return_value = {"id": "suid", "product": "premium"}
        mock_rp.return_value = {"items": []}
        mock_pl.return_value = None
        mock_cp.return_value = None
        mock_tt.return_value = {"items": []}
        mock_ta.return_value = {"items": []}
        mock_saved.return_value = 0

        user = User.objects.create_user(username="su", email="su@test.com", password="p")
        conn = SpotifyConnection.objects.create(
            user=user,
            refresh_token_encrypted=encrypt_oauth_token("r"),
            access_token_encrypted=encrypt_oauth_token("a"),
            token_expires_at=timezone.now() + timedelta(hours=1),
        )

        merchant, _ = Merchant.objects.get_or_create(
            name="Spotify",
            defaults={
                "category": MerchantCategory.STREAMING,
                "website_domain": "spotify.com",
            },
        )
        sub = Subscription.objects.create(
            user=user,
            merchant=merchant,
            price=10,
            status=SubscriptionStatus.ACTIVE,
        )

        from apps.integrations.services.spotify_sync import sync_spotify_for_user

        sync_spotify_for_user(user)
        sub.refresh_from_db()
        self.assertIsNotNone(sub.subscription_utilization)
        self.assertIsNotNone(sub.usage_frequency)
        self.assertGreaterEqual(float(sub.usage_frequency), 0.0)
        self.assertLessEqual(float(sub.usage_frequency), 1.0)
        conn.refresh_from_db()
        self.assertEqual(conn.sync_status, SpotifySyncStatus.OK)
