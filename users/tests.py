import uuid
from unittest.mock import Mock, patch
from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase


User = get_user_model()


class SupabaseUserSyncViewTests(APITestCase):
    def test_sync_returns_authenticated_user_payload(self):
        user = User.objects.create_user(
            username="jane@example.com",
            email="jane@example.com",
            supabase_uid=uuid.uuid4(),
            password="testpass123",
        )
        self.client.force_authenticate(user=user)

        response = self.client.post(reverse("supabase-user-sync"))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["id"], user.id)
        self.assertEqual(response.data["email"], user.email)
        self.assertEqual(response.data["username"], user.username)
        self.assertEqual(response.data["supabase_uid"], str(user.supabase_uid))

    def test_sync_requires_authentication(self):
        response = self.client.post(reverse("supabase-user-sync"))
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)


class SupabaseEmailExtractionTests(APITestCase):
    def test_extracts_email_only_from_top_level_payload_claim(self):
        from users.supabase_auth import _extract_email

        payload = {
            "email": "CanonicalUser@Example.com",
            "metadata": {"email": "MetaDataUser@Example.com"},
            "user_metadata": {"email": "UserMetaDataUser@Example.com"},
        }

        email = _extract_email(payload)

        self.assertEqual(email, "canonicaluser@example.com")

    def test_does_not_extract_email_from_metadata_fallbacks(self):
        from users.supabase_auth import _extract_email

        payload = {
            "metadata": {"email": "MetaDataUser@Example.com"},
            "user_metadata": {"email": "UserMetaDataUser@Example.com"},
        }

        email = _extract_email(payload)

        self.assertIsNone(email)


class SupabaseEmailVerificationTests(APITestCase):
    def test_recognizes_email_confirmed_at_claim(self):
        from users.supabase_auth import _is_email_verified

        self.assertTrue(_is_email_verified({"email_confirmed_at": "2024-01-01T00:00:00Z"}))
        self.assertFalse(_is_email_verified({"email_confirmed_at": None}))
        self.assertFalse(_is_email_verified({}))


class SupabaseUserLookupTests(APITestCase):
    @patch("users.supabase_auth.SUPABASE_URL", "https://example.supabase.co")
    @patch("users.supabase_auth.requests.get")
    def test_fetch_supabase_user_returns_json_object(self, mock_get):
        from users.supabase_auth import _fetch_supabase_user

        response = Mock()
        response.headers = {"content-type": "application/json; charset=utf-8"}
        response.json.return_value = {"email_confirmed_at": "2024-01-01T00:00:00Z"}
        response.raise_for_status.return_value = None
        mock_get.return_value = response

        user_data = _fetch_supabase_user("test-token")

        self.assertEqual(
            user_data["email_confirmed_at"],
            "2024-01-01T00:00:00Z",
        )

    @patch("users.supabase_auth.SUPABASE_URL", "https://example.supabase.co")
    @patch("users.supabase_auth.requests.get")
    def test_fetch_supabase_user_rejects_non_json_response(self, mock_get):
        from users.supabase_auth import _fetch_supabase_user
        from rest_framework.exceptions import AuthenticationFailed

        response = Mock()
        response.headers = {"content-type": "text/html"}
        response.raise_for_status.return_value = None
        mock_get.return_value = response

        with self.assertRaises(AuthenticationFailed):
            _fetch_supabase_user("test-token")


class SupabaseAuthenticationSecurityTests(APITestCase):
    def tearDown(self):
        from users import supabase_auth

        supabase_auth._JWKS_CACHE = None
        supabase_auth._JWKS_CACHE_EXPIRES_AT = 0.0

    @patch("users.supabase_auth._verify_and_decode")
    def test_rejects_implicit_email_account_claim(self, mock_verify_and_decode):
        from rest_framework.exceptions import AuthenticationFailed
        from users.supabase_auth import SupabaseJWTAuthentication

        User.objects.create_user(
            username="existing-local-user",
            email="jane@example.com",
            password="testpass123",
        )
        mock_verify_and_decode.return_value = {
            "sub": str(uuid.uuid4()),
            "email": "jane@example.com",
            "email_confirmed_at": "2024-01-01T00:00:00Z",
        }

        request = Mock()
        request.headers = {"Authorization": "Bearer test-token"}

        with self.assertRaises(AuthenticationFailed):
            SupabaseJWTAuthentication().authenticate(request)

    @patch("users.supabase_auth._verify_and_decode")
    def test_rejects_unverified_email_claim_in_token(self, mock_verify_and_decode):
        from rest_framework.exceptions import AuthenticationFailed
        from users.supabase_auth import SupabaseJWTAuthentication

        mock_verify_and_decode.return_value = {
            "sub": str(uuid.uuid4()),
            "email": "jane@example.com",
            "email_confirmed_at": None,
        }

        request = Mock()
        request.headers = {"Authorization": "Bearer test-token"}

        with self.assertRaises(AuthenticationFailed):
            SupabaseJWTAuthentication().authenticate(request)

    @patch("users.supabase_auth.SUPABASE_JWT_ISS", "")
    def test_verify_and_decode_requires_issuer_configuration(self):
        from rest_framework.exceptions import AuthenticationFailed
        from users.supabase_auth import _verify_and_decode

        with self.assertRaises(AuthenticationFailed):
            _verify_and_decode("test-token")

    @patch("users.supabase_auth.SUPABASE_URL", "https://example.supabase.co")
    @patch("users.supabase_auth.SUPABASE_JWKS_CACHE_TTL_SECONDS", 300)
    @patch("users.supabase_auth._fetch_jwks")
    def test_jwks_cache_expires_and_refreshes(self, mock_fetch_jwks):
        from users import supabase_auth

        mock_fetch_jwks.side_effect = [
            {"keys": [{"kid": "old"}]},
            {"keys": [{"kid": "new"}]},
        ]

        with patch("users.supabase_auth.time.time", side_effect=[100.0, 100.0, 401.0, 401.0]):
            first_keys = supabase_auth._get_jwks()
            second_keys = supabase_auth._get_jwks()

        self.assertEqual(first_keys["keys"][0]["kid"], "old")
        self.assertEqual(second_keys["keys"][0]["kid"], "new")
        self.assertEqual(mock_fetch_jwks.call_count, 2)
