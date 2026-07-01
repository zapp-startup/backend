import time
import uuid
from decimal import Decimal
from unittest.mock import Mock, patch

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import override_settings
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from apps.users.session_auth import AUTH_SESSION_KEY

User = get_user_model()


class SupabaseUserSyncViewTests(APITestCase):
    def test_sync_returns_authenticated_user_profile(self):
        user = User.objects.create_user(
            username="sync-user",
            email="sync@example.com",
            password="unused-password",
        )
        self.client.force_authenticate(
            user=user,
            token={
                "supabase_uid": "12345678-1234-5678-1234-567812345678",
                "aal": "aal2",
                "mfa_factors_count": 1,
            },
        )

        response = self.client.post(reverse("supabase-user-sync"))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["id"], user.id)
        self.assertEqual(response.data["email"], user.email)
        self.assertEqual(response.data["username"], user.username)
        self.assertEqual(response.data["supabase_uid"], user.supabase_uid)
        self.assertEqual(response.data["next_step"], "onboarding_survey")
        self.assertFalse(response.data["onboarding_completed"])

    def test_sync_requires_aal2_when_policy_enabled(self):
        user = User.objects.create_user(
            username="sync-user-aal1",
            email="sync-aal1@example.com",
            password="unused-password",
        )
        self.client.force_authenticate(
            user=user,
            token={
                "supabase_uid": "12345678-1234-5678-1234-567812345678",
                "aal": "aal1",
                "mfa_factors_count": 0,
            },
        )

        response = self.client.post(reverse("supabase-user-sync"))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["error_code"], "mfa_enrollment_required")
        self.assertTrue(response.data["mfa_pending"])
        self.assertEqual(response.data["next_step"], "mfa_setup")
        self.assertEqual(response.data["post_mfa_step"], "onboarding_survey")

    @override_settings(AUTH_REQUIRE_AAL2=False)
    def test_sync_allows_aal1_when_policy_disabled(self):
        user = User.objects.create_user(
            username="sync-user-aal1-off",
            email="sync-aal1-off@example.com",
            password="unused-password",
        )
        self.client.force_authenticate(
            user=user,
            token={
                "supabase_uid": "12345678-1234-5678-1234-567812345678",
                "aal": "aal1",
                "mfa_factors_count": 0,
            },
        )

        response = self.client.post(reverse("supabase-user-sync"))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["email"], user.email)

    def test_sync_requires_authentication(self):
        response = self.client.post(reverse("supabase-user-sync"))
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_sync_blocks_when_session_state_is_pending_mfa(self):
        user = User.objects.create_user(
            username="sync-session-pending",
            email="sync-session-pending@example.com",
            password="unused-password",
        )
        session = self.client.session
        session[AUTH_SESSION_KEY] = {
            "user_id": user.pk,
            "aal": "aal1",
            "mfa_factor_count": 0,
            "mfa_pending": True,
            "next_aal": "aal2",
            "mfa_enrollment_required": True,
        }
        session.save()

        response = self.client.post(reverse("supabase-user-sync"))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["error_code"], "mfa_enrollment_required")
        self.assertTrue(response.data["mfa_pending"])
        self.assertEqual(response.data["auth_source"], "session")
        self.assertEqual(response.data["next_step"], "mfa_setup")
        self.assertEqual(response.data["post_mfa_step"], "onboarding_survey")

    def test_sync_normalizes_pending_verify_state_without_usable_factor_to_setup(self):
        user = User.objects.create_user(
            username="sync-session-normalized",
            email="sync-session-normalized@example.com",
            password="unused-password",
        )
        session = self.client.session
        session[AUTH_SESSION_KEY] = {
            "user_id": user.pk,
            "aal": "aal1",
            "mfa_factor_count": 0,
            "mfa_pending": True,
            "next_aal": "aal2",
            "mfa_enrollment_required": False,
        }
        session.save()

        response = self.client.post(reverse("supabase-user-sync"))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["error_code"], "mfa_enrollment_required")
        self.assertTrue(response.data["mfa_pending"])
        self.assertTrue(response.data["mfa_enrollment_required"])
        self.assertEqual(response.data["next_step"], "mfa_setup")

    def test_sync_allows_when_session_state_is_aal2(self):
        user = User.objects.create_user(
            username="sync-session-aal2",
            email="sync-session-aal2@example.com",
            password="unused-password",
        )
        session = self.client.session
        session[AUTH_SESSION_KEY] = {
            "user_id": user.pk,
            "aal": "aal2",
            "mfa_factor_count": 1,
            "mfa_pending": False,
            "next_aal": None,
            "mfa_enrollment_required": False,
        }
        session.save()

        response = self.client.post(reverse("supabase-user-sync"))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["email"], user.email)
        self.assertEqual(response.data["next_step"], "onboarding_survey")


class PendingMfaOnboardingAccessTests(APITestCase):
    def test_raw_explicit_list_allows_pending_mfa_session(self):
        user = User.objects.create_user(
            username="pending-onboarding-user",
            email="pending-onboarding@example.com",
            password="unused-password",
        )
        session = self.client.session
        session[AUTH_SESSION_KEY] = {
            "user_id": user.pk,
            "aal": "aal1",
            "mfa_factor_count": 0,
            "mfa_pending": True,
            "next_aal": "aal2",
            "mfa_enrollment_required": True,
            "_supabase_access_token": "tok",
            "_supabase_refresh_token": "rt",
            "_supabase_token_expires_at": 9999999999,
        }
        session.save()

        response = self.client.get(reverse("raw-explicit-list"))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.json(), [])


class SupabaseEmailExtractionTests(APITestCase):
    def test_extracts_email_only_from_top_level_payload_claim(self):
        from apps.users.supabase_auth import _extract_email

        payload = {
            "email": "CanonicalUser@Example.com",
            "metadata": {"email": "MetaDataUser@Example.com"},
            "user_metadata": {"email": "UserMetaDataUser@Example.com"},
        }

        email = _extract_email(payload)

        self.assertEqual(email, "canonicaluser@example.com")

    def test_does_not_extract_email_from_metadata_fallbacks(self):
        from apps.users.supabase_auth import _extract_email

        payload = {
            "metadata": {"email": "MetaDataUser@Example.com"},
            "user_metadata": {"email": "UserMetaDataUser@Example.com"},
        }

        email = _extract_email(payload)

        self.assertIsNone(email)


class SupabaseEmailVerificationTests(APITestCase):
    def test_recognizes_email_confirmed_at_claim(self):
        from apps.users.supabase_auth import _is_email_verified

        self.assertTrue(_is_email_verified({"email_confirmed_at": "2024-01-01T00:00:00Z"}))
        self.assertFalse(_is_email_verified({"email_confirmed_at": None}))
        self.assertFalse(_is_email_verified({}))


class SupabaseUserLookupTests(APITestCase):
    @patch("apps.users.supabase_auth.SUPABASE_URL", "https://example.supabase.co")
    @patch("apps.users.supabase_auth.requests.get")
    def test_fetch_supabase_user_returns_json_object(self, mock_get):
        from apps.users.supabase_auth import _fetch_supabase_user

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

    @patch("apps.users.supabase_auth.SUPABASE_URL", "https://example.supabase.co")
    @patch("apps.users.supabase_auth.requests.get")
    def test_fetch_supabase_user_rejects_non_json_response(self, mock_get):
        from apps.users.supabase_auth import _fetch_supabase_user
        from rest_framework.exceptions import AuthenticationFailed

        response = Mock()
        response.headers = {"content-type": "text/html"}
        response.raise_for_status.return_value = None
        mock_get.return_value = response

        with self.assertRaises(AuthenticationFailed):
            _fetch_supabase_user("test-token")


class SupabaseAuthenticationSecurityTests(APITestCase):
    def tearDown(self):
        from apps.users import supabase_auth

        supabase_auth._JWKS_CACHE = None
        supabase_auth._JWKS_CACHE_EXPIRES_AT = 0.0
        supabase_auth._LAST_JWKS_REFRESH_ATTEMPT = 0.0

    @patch("apps.users.supabase_auth._verify_and_decode")
    def test_rejects_implicit_email_account_claim(self, mock_verify_and_decode):
        from rest_framework.exceptions import AuthenticationFailed
        from apps.users.supabase_auth import SupabaseJWTAuthentication

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

    @patch("apps.users.supabase_auth._verify_and_decode")
    def test_rejects_unverified_email_claim_in_token(self, mock_verify_and_decode):
        from rest_framework.exceptions import AuthenticationFailed
        from apps.users.supabase_auth import SupabaseJWTAuthentication

        mock_verify_and_decode.return_value = {
            "sub": str(uuid.uuid4()),
            "email": "jane@example.com",
            "email_confirmed_at": None,
        }

        request = Mock()
        request.headers = {"Authorization": "Bearer test-token"}

        with self.assertRaises(AuthenticationFailed):
            SupabaseJWTAuthentication().authenticate(request)

    @patch("apps.users.supabase_auth.SUPABASE_JWT_ISS", "")
    def test_verify_and_decode_requires_issuer_configuration(self):
        from rest_framework.exceptions import AuthenticationFailed
        from apps.users.supabase_auth import _verify_and_decode

        with self.assertRaises(AuthenticationFailed):
            _verify_and_decode("test-token")

    @patch("apps.users.supabase_auth.SUPABASE_URL", "https://example.supabase.co")
    @patch("apps.users.supabase_auth.SUPABASE_JWKS_CACHE_TTL_SECONDS", 300)
    @patch("apps.users.supabase_auth._fetch_jwks")
    def test_jwks_cache_expires_and_refreshes(self, mock_fetch_jwks):
        from apps.users import supabase_auth

        mock_fetch_jwks.side_effect = [
            {"keys": [{"kid": "old"}]},
            {"keys": [{"kid": "new"}]},
        ]

        with patch("apps.users.supabase_auth.time.time", side_effect=[100.0, 100.0, 401.0, 401.0]):
            first_keys = supabase_auth._get_jwks()
            second_keys = supabase_auth._get_jwks()

        self.assertEqual(first_keys["keys"][0]["kid"], "old")
        self.assertEqual(second_keys["keys"][0]["kid"], "new")
        self.assertEqual(mock_fetch_jwks.call_count, 2)

    @patch("apps.users.supabase_auth.SUPABASE_URL", "https://example.supabase.co")
    @patch("apps.users.supabase_auth._fetch_jwks")
    def test_jwks_retries_immediately_on_cold_start_after_failure(self, mock_fetch_jwks):
        from rest_framework.exceptions import AuthenticationFailed
        from apps.users import supabase_auth

        mock_fetch_jwks.side_effect = [
            AuthenticationFailed("Unable to validate Supabase token."),
            {"keys": [{"kid": "new"}]},
        ]

        keys = supabase_auth._get_jwks_with_refresh(force_refresh=True)

        self.assertEqual(keys["keys"][0]["kid"], "new")
        self.assertEqual(mock_fetch_jwks.call_count, 2)

    @patch("apps.users.supabase_auth.SUPABASE_URL", "https://example.supabase.co")
    @patch("apps.users.supabase_auth._fetch_jwks")
    def test_jwks_backoff_only_applies_when_cache_exists(self, mock_fetch_jwks):
        from apps.users import supabase_auth

        supabase_auth._JWKS_CACHE = {"keys": [{"kid": "cached"}]}
        supabase_auth._JWKS_CACHE_EXPIRES_AT = 0.0
        supabase_auth._LAST_JWKS_REFRESH_ATTEMPT = time.time()

        keys = supabase_auth._get_jwks_with_refresh(force_refresh=True)

        self.assertEqual(keys["keys"][0]["kid"], "cached")
        mock_fetch_jwks.assert_not_called()


class UserPreferenceSerializerValidationTests(APITestCase):
    def test_purchase_advisor_logic_requires_boolean_enabled(self):
        from apps.users.serializers import UserPreferenceSerializer

        serializer = UserPreferenceSerializer(data={
            "key": "purchase_advisor_logic",
            "value_type": "json",
            "value": {"enabled": "yes"},
            "source": "manual",
            "confidence": 1.0,
        })

        self.assertFalse(serializer.is_valid())
        self.assertIn("value", serializer.errors)


class EncryptedFieldStorageTests(APITestCase):
    def test_user_raw_explicit_sensitive_fields_are_stored_encrypted(self):
        from cryptography.fernet import Fernet
        from apps.users.models import UserRawExplicit

        user = User.objects.create_user(username="enc-user", email="enc@example.com", password="x")
        key = Fernet.generate_key().decode("ascii")

        with self.settings(APP_DATA_ENCRYPTION_KEY=key):
            UserRawExplicit.objects.create(
                user=user,
                display_name="Alice",
                monthly_income=Decimal("5000.25"),
                location_zip="60601",
            )

            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT display_name, monthly_income, location_zip FROM users_userrawexplicit WHERE user_id = %s",
                    [user.pk],
                )
                display_name, monthly_income, location_zip = cursor.fetchone()

            self.assertNotEqual(display_name, "Alice")
            self.assertTrue(str(display_name).startswith("gAAAA"))
            self.assertTrue(str(monthly_income).startswith("gAAAA"))
            self.assertTrue(str(location_zip).startswith("gAAAA"))

    def test_transaction_sensitive_fields_are_stored_encrypted(self):
        from cryptography.fernet import Fernet
        from apps.transactions.models import Transaction

        user = User.objects.create_user(username="txn-user", email="txn@example.com", password="x")
        key = Fernet.generate_key().decode("ascii")

        with self.settings(APP_DATA_ENCRYPTION_KEY=key):
            txn = Transaction.objects.create(
                user=user,
                direction="spend",
                amount=Decimal("19.99"),
                occurred_at="2026-03-31T10:00:00Z",
                category="shopping",
                description_raw="Order 123",
                reflection_text="Probably impulsive",
            )

            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT amount, description_raw, reflection_text FROM transactions_transaction WHERE id = %s",
                    [txn.pk],
                )
                amount, description_raw, reflection_text = cursor.fetchone()

            self.assertTrue(str(amount).startswith("gAAAA"))
            self.assertTrue(str(description_raw).startswith("gAAAA"))
            self.assertTrue(str(reflection_text).startswith("gAAAA"))


    def test_purchase_advisor_logic_rejects_unknown_focus_categories(self):
        from apps.users.serializers import UserPreferenceSerializer

        serializer = UserPreferenceSerializer(data={
            "key": "purchase_advisor_logic",
            "value_type": "json",
            "value": {"enabled": True, "focus_categories": ["shopping"]},
            "source": "manual",
            "confidence": 1.0,
        })

        self.assertFalse(serializer.is_valid())
        self.assertIn("value", serializer.errors)
