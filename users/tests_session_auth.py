"""Tests for session BFF auth service and API views."""
from __future__ import annotations

import base64
import uuid
from urllib.parse import parse_qs, urlparse
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth import SESSION_KEY as AUTH_USER_SESSION_KEY, get_user_model
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient, APIRequestFactory

from banking.exceptions import MfaVerificationNeededException
from banking.security_checks import enforce_banking_policies
from users.security_assurance import banking_step_up_fresh, extract_assurance_from_request
from users.auth_views import _complete_mfa_verification
from users.models import UserRawExplicit
from users.session_auth import (
    AUTH_SESSION_KEY,
    SupabaseAuthError,
    get_or_create_local_user,
    issue_django_session,
    sync_session_assurance_from_user,
)
from zapp.security.data_encryption import decrypt_app_data

User = get_user_model()


def _add_session_to_request(request):
    from django.contrib.sessions.middleware import SessionMiddleware

    middleware = SessionMiddleware(lambda req: None)
    middleware.process_request(request)
    request.session.save()


def _csrf_header(client):
    token = client.cookies.get("csrftoken")
    return {"HTTP_X_CSRFTOKEN": token.value} if token else {}


def _assert_redirect_location(testcase, location: str, expected_base: str, expected_params: dict[str, str]):
    parsed = urlparse(location)
    actual_base = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
    testcase.assertEqual(actual_base, expected_base)
    params = parse_qs(parsed.query, keep_blank_values=True)
    for key, value in expected_params.items():
        testcase.assertEqual(params.get(key), [value], f"missing/invalid redirect param: {key}")


class GetOrCreateLocalUserTests(TestCase):
    def test_creates_user_when_new(self):
        uid = uuid.uuid4()
        user = get_or_create_local_user(uid, "new@example.com")
        self.assertEqual(user.email, "new@example.com")
        self.assertEqual(user.supabase_uid, uid)

    def test_returns_existing_by_supabase_uid(self):
        uid = uuid.uuid4()
        u1 = get_or_create_local_user(uid, "same@example.com")
        u2 = get_or_create_local_user(uid, "same@example.com")
        self.assertEqual(u1.pk, u2.pk)

    def test_email_conflict_raises(self):
        uid = uuid.uuid4()
        User.objects.create_user(
            username="other",
            email="taken@example.com",
            password="x",
        )
        with self.assertRaises(SupabaseAuthError) as ctx:
            get_or_create_local_user(uid, "taken@example.com")
        self.assertEqual(ctx.exception.error_code, "email_link_conflict")


class IssueDjangoSessionTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="sess_u",
            email="sess@example.com",
            password=None,
        )
        self.user.supabase_uid = uuid.uuid4()
        self.user.save()

    def test_flush_and_stores_auth_block(self):
        factory = RequestFactory()
        request = factory.post("/")
        _add_session_to_request(request)
        request.session["old"] = True
        request.session.save()

        issue_django_session(
            request,
            self.user,
            {"aal": "aal1", "auth_method": "password", "mfa_factor_count": 0},
            "access-token",
            "refresh-token",
            3600,
        )

        self.assertNotIn("old", request.session)
        block = request.session.get(AUTH_SESSION_KEY)
        self.assertIsNotNone(block)
        self.assertEqual(block["user_id"], self.user.pk)
        self.assertEqual(block["_supabase_access_token"], "access-token")
        self.assertEqual(block["aal"], "aal1")
        self.assertIsNone(block["last_step_up_at"])

    def test_aal2_sets_last_step_up(self):
        factory = RequestFactory()
        request = factory.post("/")
        _add_session_to_request(request)
        issue_django_session(
            request,
            self.user,
            {"aal": "aal2", "auth_method": "password", "mfa_factor_count": 1},
            "at",
            "rt",
            3600,
        )
        block = request.session[AUTH_SESSION_KEY]
        self.assertIsNotNone(block["last_step_up_at"])

    @override_settings(APP_DATA_ENCRYPTION_KEY="8bUpWwzYgUN7ctklDvqGELWMKhfYbsxxNaKzUknYI5Q=")
    def test_issue_django_session_encrypts_upstream_tokens_when_key_configured(self):
        factory = RequestFactory()
        request = factory.post("/")
        _add_session_to_request(request)
        issue_django_session(
            request,
            self.user,
            {"aal": "aal1", "auth_method": "password", "mfa_factor_count": 1},
            "access-token",
            "refresh-token",
            3600,
        )

        block = request.session[AUTH_SESSION_KEY]
        self.assertNotEqual(block["_supabase_access_token"], "access-token")
        self.assertNotEqual(block["_supabase_refresh_token"], "refresh-token")
        self.assertEqual(decrypt_app_data(block["_supabase_access_token"])[0], "access-token")
        self.assertEqual(decrypt_app_data(block["_supabase_refresh_token"])[0], "refresh-token")


class BankingStepUpFreshTests(TestCase):
    def test_jwt_source_skips_freshness(self):
        info = {
            "aal": "aal2",
            "_assurance_source": "jwt",
            "last_step_up_at": None,
        }
        with override_settings(BANKING_STEP_UP_REQUIRED=True):
            self.assertTrue(banking_step_up_fresh(info))

    def test_session_aal2_without_timestamp_stale(self):
        info = {
            "aal": "aal2",
            "_assurance_source": "session",
            "last_step_up_at": None,
        }
        with override_settings(BANKING_STEP_UP_REQUIRED=True):
            self.assertFalse(banking_step_up_fresh(info))

    def test_session_aal2_recent_fresh(self):
        import time

        now = int(time.time())
        info = {
            "aal": "aal2",
            "_assurance_source": "session",
            "last_step_up_at": now - 10,
        }
        with override_settings(
            BANKING_STEP_UP_REQUIRED=True,
            BANKING_STEP_UP_FRESHNESS_SECONDS=900,
        ):
            self.assertTrue(banking_step_up_fresh(info))


class EnforceBankingPoliciesSessionTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="bank_u",
            email="bank@example.com",
            password="x",
        )

    def _request_with_session_auth(self, session_block):
        factory = APIRequestFactory()
        request = factory.post("/api/banking/link-token/")
        _add_session_to_request(request)
        request.session[AUTH_SESSION_KEY] = session_block
        request.session.save()
        request.user = self.user
        return request

    @override_settings(
        BANKING_REQUIRE_MFA=True,
        BANKING_REQUIRE_FINANCIAL_CONSENT=False,
        BANKING_STEP_UP_REQUIRED=True,
        BANKING_STEP_UP_FRESHNESS_SECONDS=900,
    )
    def test_session_aal2_stale_raises(self):
        import time

        request = self._request_with_session_auth(
            {
                "user_id": self.user.pk,
                "aal": "aal2",
                "mfa_factor_count": 1,
                "last_step_up_at": int(time.time()) - 10_000,
            }
        )
        with self.assertRaises(MfaVerificationNeededException):
            enforce_banking_policies(request)

    @override_settings(
        BANKING_REQUIRE_MFA=True,
        BANKING_REQUIRE_FINANCIAL_CONSENT=False,
        BANKING_STEP_UP_REQUIRED=True,
        BANKING_STEP_UP_FRESHNESS_SECONDS=900,
    )
    def test_session_aal2_fresh_ok(self):
        import time

        request = self._request_with_session_auth(
            {
                "user_id": self.user.pk,
                "aal": "aal2",
                "mfa_factor_count": 1,
                "last_step_up_at": int(time.time()) - 60,
            }
        )
        enforce_banking_policies(request)


class LoginViewTests(TestCase):
    def setUp(self):
        self.client = APIClient(enforce_csrf_checks=True)

    @patch("users.auth_views.supabase_login")
    @patch("users.auth_views.audit_login")
    def test_login_enters_pending_mfa_session_until_aal2(self, mock_audit, mock_login):
        uid = str(uuid.uuid4())
        mock_login.return_value = {
            "access_token": "at",
            "refresh_token": "rt",
            "expires_in": 3600,
            "user": {
                "id": uid,
                "email": "login@example.com",
                "email_confirmed_at": "2020-01-01T00:00:00Z",
                "aal": "aal1",
                "factors": [],
            },
        }
        self.client.get(reverse("auth-csrf"))
        r = self.client.post(
            reverse("auth-login"),
            {"email": "login@example.com", "password": "secret"},
            format="json",
            **_csrf_header(self.client),
        )
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        self.assertEqual(r.json()["user"]["email"], "login@example.com")
        self.assertTrue(r.json()["mfa_pending"])
        self.assertTrue(r.json()["mfa_enrollment_required"])
        self.assertEqual(r.json()["error_code"], "mfa_enrollment_required")
        self.assertEqual(r.json()["next_step"], "mfa_setup")
        self.assertEqual(r.json()["post_mfa_step"], "onboarding_survey")
        self.assertNotIn("access_token", r.json())

        me_response = self.client.get(reverse("auth-me"))
        self.assertEqual(me_response.status_code, status.HTTP_200_OK)
        self.assertTrue(me_response.json()["mfa_pending"])
        self.assertEqual(me_response.json()["next_step"], "mfa_setup")

        sync_response = self.client.post(
            reverse("supabase-user-sync"),
            {},
            format="json",
            **_csrf_header(self.client),
        )
        self.assertEqual(sync_response.status_code, status.HTTP_200_OK)
        self.assertTrue(sync_response.json()["mfa_pending"])
        self.assertEqual(sync_response.json()["next_step"], "mfa_setup")
        self.assertEqual(sync_response.json()["post_mfa_step"], "onboarding_survey")

    @patch("users.auth_views.supabase_login")
    @patch("users.auth_views.audit_login")
    def test_login_with_only_unverified_factor_still_routes_to_mfa_setup(self, mock_audit, mock_login):
        uid = str(uuid.uuid4())
        mock_login.return_value = {
            "access_token": "at",
            "refresh_token": "rt",
            "expires_in": 3600,
            "user": {
                "id": uid,
                "email": "login@example.com",
                "email_confirmed_at": "2020-01-01T00:00:00Z",
                "aal": "aal1",
                "factors": [{"id": "factor-1", "status": "unverified"}],
            },
        }

        self.client.get(reverse("auth-csrf"))
        response = self.client.post(
            reverse("auth-login"),
            {"email": "login@example.com", "password": "secret"},
            format="json",
            **_csrf_header(self.client),
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.json()["mfa_factor_count"], 0)
        self.assertTrue(response.json()["mfa_pending"])
        self.assertTrue(response.json()["mfa_enrollment_required"])
        self.assertEqual(response.json()["next_step"], "mfa_setup")

    @patch("users.auth_views.supabase_login")
    @patch("users.auth_views.audit_login")
    def test_login_supabase_error(self, mock_audit, mock_login):
        mock_login.side_effect = SupabaseAuthError("bad", error_code="invalid_credentials")
        self.client.get(reverse("auth-csrf"))
        r = self.client.post(
            reverse("auth-login"),
            {"email": "a@b.com", "password": "x"},
            format="json",
        )
        self.assertEqual(r.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(r.json().get("error_code"), "invalid_credentials")


class MeViewTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(
            username="me_u",
            email="me@example.com",
            password="x",
        )

    def test_me_anonymous_401(self):
        r = self.client.get(reverse("auth-me"))
        # SessionAuthentication is listed first; unauthenticated may be 401 or 403.
        self.assertIn(r.status_code, (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN))

    def test_me_authenticated_session(self):
        self.client.force_login(self.user)
        session = self.client.session
        session[AUTH_SESSION_KEY] = {
            "user_id": self.user.pk,
            "aal": "aal2",
            "mfa_factor_count": 1,
            "last_step_up_at": 123,
            "logged_in_at": 456,
        }
        session.save()
        r = self.client.get(reverse("auth-me"))
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        self.assertEqual(r.json()["email"], "me@example.com")
        self.assertEqual(r.json()["aal"], "aal2")
        self.assertEqual(r.json()["name"], "me_u")

    def test_me_pending_mfa_session(self):
        session = self.client.session
        session[AUTH_SESSION_KEY] = {
            "user_id": self.user.pk,
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

        r = self.client.get(reverse("auth-me"))
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        self.assertEqual(r.json()["email"], "me@example.com")
        self.assertTrue(r.json()["mfa_pending"])
        self.assertTrue(r.json()["mfa_enrollment_required"])

    def test_me_uses_saved_display_name(self):
        self.user.first_name = "Saved Display Name"
        self.user.save(update_fields=["first_name"])

        self.client.force_login(self.user)
        r = self.client.get(reverse("auth-me"))

        self.assertEqual(r.status_code, status.HTTP_200_OK)
        self.assertEqual(r.json()["name"], "Saved Display Name")

    def test_patch_me_updates_display_name(self):
        self.client.force_login(self.user)

        r = self.client.patch(
            reverse("auth-me"),
            {"name": "Updated Display Name"},
            format="json",
        )

        self.assertEqual(r.status_code, status.HTTP_200_OK)
        self.user.refresh_from_db()
        self.assertEqual(self.user.first_name, "Updated Display Name")
        self.assertEqual(r.json()["name"], "Updated Display Name")

    def test_patch_me_syncs_existing_raw_explicit_display_name(self):
        UserRawExplicit.objects.create(user=self.user, display_name="Old Name")
        self.client.force_login(self.user)

        r = self.client.patch(
            reverse("auth-me"),
            {"name": "Updated Display Name"},
            format="json",
        )

        self.assertEqual(r.status_code, status.HTTP_200_OK)
        explicit = UserRawExplicit.objects.get(user=self.user)
        self.assertEqual(explicit.display_name, "Updated Display Name")

    def test_patch_me_does_not_create_raw_explicit(self):
        self.client.force_login(self.user)

        r = self.client.patch(
            reverse("auth-me"),
            {"name": "Updated Display Name"},
            format="json",
        )

        self.assertEqual(r.status_code, status.HTTP_200_OK)
        self.assertFalse(UserRawExplicit.objects.filter(user=self.user).exists())


class MfaVerifySessionUpgradeTests(TestCase):
    """MFA verify must persist aal2 + tokens; snapshot sync must not regress assurance."""

    def setUp(self):
        self.user = User.objects.create_user(
            username="mfa_verify_u",
            email="mfa_verify@example.com",
            password="x",
        )
        self.user.supabase_uid = uuid.uuid4()
        self.user.save()

    def _pending_mfa_request(self):
        factory = RequestFactory()
        request = factory.post("/")
        _add_session_to_request(request)
        request.session[AUTH_SESSION_KEY] = {
            "user_id": self.user.pk,
            "aal": "aal1",
            "mfa_factor_count": 1,
            "mfa_pending": True,
            "next_aal": "aal2",
            "mfa_enrollment_required": False,
            "auth_method": "supabase_password",
            "_supabase_access_token": "old-access-token",
            "_supabase_refresh_token": "old-refresh-token",
            "_supabase_token_expires_at": 9999999999,
        }
        request.session.save()
        request.user = self.user
        return request

    def test_complete_mfa_verify_stores_aal2_in_session(self):
        request = self._pending_mfa_request()
        user_obj = {
            "id": str(self.user.supabase_uid),
            "email": "mfa_verify@example.com",
            "email_confirmed_at": "2020-01-01T00:00:00Z",
            "factors": [{"id": "f1", "status": "verified"}],
        }
        verify_out = {
            "access_token": "new-aal2-access-token",
            "refresh_token": "new-refresh-token",
            "expires_in": 3600,
        }
        _complete_mfa_verification(request, user_obj=user_obj, verify_out=verify_out)
        block = request.session.get(AUTH_SESSION_KEY)
        self.assertIsNotNone(block)
        self.assertEqual(block["aal"], "aal2")
        self.assertIsNotNone(block.get("last_step_up_at"))
        self.assertFalse(block.get("mfa_pending"))
        self.assertEqual(
            decrypt_app_data(block["_supabase_access_token"])[0],
            "new-aal2-access-token",
        )

    def test_aal2_survives_sync_after_verify(self):
        request = self._pending_mfa_request()
        user_obj = {
            "id": str(self.user.supabase_uid),
            "email": "mfa_verify@example.com",
            "email_confirmed_at": "2020-01-01T00:00:00Z",
            "factors": [{"id": "f1", "status": "verified"}],
        }
        verify_out = {
            "access_token": "new-aal2-access-token",
            "refresh_token": "new-refresh-token",
            "expires_in": 3600,
        }
        _complete_mfa_verification(request, user_obj=user_obj, verify_out=verify_out)
        # Real GET /auth/v1/user often omits top-level aal (it lives in the JWT).
        user_obj_no_aal = {
            "email_confirmed_at": "2020-01-01T00:00:00Z",
            "factors": [{"id": "f1", "status": "verified"}],
        }
        sync_session_assurance_from_user(request, user_obj_no_aal)
        block = request.session[AUTH_SESSION_KEY]
        self.assertEqual(block["aal"], "aal2")
        self.assertFalse(block.get("mfa_pending"))

    def test_snapshot_sync_does_not_clear_django_auth_keys(self):
        request = self._pending_mfa_request()
        user_obj = {
            "id": str(self.user.supabase_uid),
            "email": "mfa_verify@example.com",
            "email_confirmed_at": "2020-01-01T00:00:00Z",
            "factors": [{"id": "f1", "status": "verified"}],
        }
        verify_out = {
            "access_token": "new-aal2-access-token",
            "refresh_token": "new-refresh-token",
            "expires_in": 3600,
        }
        _complete_mfa_verification(request, user_obj=user_obj, verify_out=verify_out)
        self.assertIn(AUTH_USER_SESSION_KEY, request.session)
        sync_session_assurance_from_user(
            request,
            {
                "email_confirmed_at": "2020-01-01T00:00:00Z",
                "factors": [{"id": "f1", "status": "verified"}],
            },
        )
        self.assertIn(AUTH_USER_SESSION_KEY, request.session)

    @override_settings(AUTH_REQUIRE_AAL2=True)
    def test_onboarding_blocked_until_mfa_complete(self):
        client = APIClient()
        session = client.session
        session[AUTH_SESSION_KEY] = {
            "user_id": self.user.pk,
            "aal": "aal1",
            "mfa_factor_count": 1,
            "mfa_pending": True,
            "next_aal": "aal2",
            "mfa_enrollment_required": False,
            "_supabase_access_token": "tok",
            "_supabase_refresh_token": "rt",
            "_supabase_token_expires_at": 9999999999,
        }
        session.save()
        r = client.get(reverse("auth-me"))
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        self.assertEqual(r.json()["next_step"], "mfa_verify")
        self.assertNotEqual(r.json()["next_step"], "onboarding_survey")


class SignupViewTests(TestCase):
    def setUp(self):
        self.client = APIClient(enforce_csrf_checks=True)

    @override_settings(
        SUPABASE_EMAIL_CONFIRM_CALLBACK_URI="http://127.0.0.1:8000/api/auth/email/confirm/",
        SUPABASE_EMAIL_CONFIRM_REDIRECT_TO="http://127.0.0.1:5173/onboarding",
        ONBOARDING_AFTER_COMPLETE_REDIRECT_TO="/dashboard",
    )
    @patch("users.auth_views.audit_login")
    @patch("users.auth_views.supabase_signup")
    def test_signup_enters_pending_mfa_enrollment_session(self, mock_signup, mock_audit):
        uid = str(uuid.uuid4())
        mock_signup.return_value = {
            "access_token": "signup-at",
            "refresh_token": "signup-rt",
            "expires_in": 3600,
            "user": {
                "id": uid,
                "email": "signup@example.com",
                "email_confirmed_at": "2020-01-01T00:00:00Z",
                "aal": "aal1",
                "factors": [],
            },
        }
        self.client.get(reverse("auth-csrf"))
        response = self.client.post(
            reverse("auth-signup"),
            {"email": "signup@example.com", "password": "secret"},
            format="json",
            **_csrf_header(self.client),
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.json()["mfa_pending"])
        self.assertEqual(response.json()["error_code"], "mfa_enrollment_required")
        self.assertEqual(
            mock_signup.call_args.kwargs["email_redirect_to"],
            "http://127.0.0.1:8000/api/auth/email/confirm/",
        )

    @override_settings(
        SUPABASE_EMAIL_CONFIRM_CALLBACK_URI="http://127.0.0.1:8000/api/auth/email/confirm/",
        SUPABASE_EMAIL_CONFIRM_REDIRECT_TO="http://127.0.0.1:5173/onboarding",
        ONBOARDING_AFTER_COMPLETE_REDIRECT_TO="/dashboard",
    )
    @patch("users.auth_views.supabase_signup")
    def test_signup_email_confirmation_response_includes_onboarding_redirect(self, mock_signup):
        mock_signup.return_value = {}
        self.client.get(reverse("auth-csrf"))
        response = self.client.post(
            reverse("auth-signup"),
            {"email": "signup@example.com", "password": "secret"},
            format="json",
            **_csrf_header(self.client),
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.json()["email_confirmation_required"])
        self.assertTrue(response.json()["requires_verification"])
        self.assertEqual(
            response.json()["email_confirmation_redirect_to"],
            "http://127.0.0.1:5173/onboarding?next=%2Fdashboard",
        )
        self.assertEqual(
            mock_signup.call_args.kwargs["email_redirect_to"],
            "http://127.0.0.1:8000/api/auth/email/confirm/",
        )


class EmailConfirmCallbackViewTests(TestCase):
    def setUp(self):
        self.client = APIClient()

    @override_settings(
        SUPABASE_EMAIL_CONFIRM_REDIRECT_TO="http://127.0.0.1:5173/onboarding",
        ONBOARDING_AFTER_COMPLETE_REDIRECT_TO="/dashboard",
    )
    def test_email_confirm_callback_missing_token_redirects_with_error(self):
        response = self.client.get(reverse("auth-email-confirm-callback"), follow=False)
        self.assertEqual(response.status_code, status.HTTP_302_FOUND)
        _assert_redirect_location(
            self,
            response["Location"],
            "http://127.0.0.1:5173/onboarding",
            {
                "next": "/dashboard",
                "login": "email_confirm_missing_token",
                "next_aal": "aal2",
            },
        )

    @override_settings(
        SUPABASE_EMAIL_CONFIRM_REDIRECT_TO="http://127.0.0.1:5173/onboarding",
        ONBOARDING_AFTER_COMPLETE_REDIRECT_TO="/dashboard",
    )
    @patch("users.auth_views.audit_login")
    @patch("users.auth_views.supabase_verify_email_token")
    def test_email_confirm_callback_sets_pending_session_and_redirects(
        self,
        mock_verify_email,
        mock_audit,
    ):
        uid = str(uuid.uuid4())
        mock_verify_email.return_value = {
            "access_token": "confirm-at",
            "refresh_token": "confirm-rt",
            "expires_in": 3600,
            "user": {
                "id": uid,
                "email": "confirm@example.com",
                "email_confirmed_at": "2020-01-01T00:00:00Z",
                "aal": "aal1",
                "factors": [],
            },
        }

        response = self.client.get(
            reverse("auth-email-confirm-callback"),
            {"token_hash": "token-hash", "type": "signup"},
            follow=False,
        )

        self.assertEqual(response.status_code, status.HTTP_302_FOUND)
        _assert_redirect_location(
            self,
            response["Location"],
            "http://127.0.0.1:5173/onboarding",
            {
                "next": "/dashboard",
                "login": "mfa_enrollment_required",
                "next_aal": "aal2",
            },
        )
        me_response = self.client.get(reverse("auth-me"))
        self.assertEqual(me_response.status_code, status.HTTP_200_OK)
        self.assertEqual(me_response.json()["email"], "confirm@example.com")
        self.assertTrue(me_response.json()["mfa_pending"])


class AuthJourneyRegressionTests(TestCase):
    def setUp(self):
        self.client = APIClient(enforce_csrf_checks=True)
        self.client.get(reverse("auth-csrf"))

    def _onboarding_payload(self):
        return {
            "display_name": "Journey User",
            "life_stage": "early_career",
            "household_size": 2,
            "location_zip": "60601",
            "income_range": "$4k-7k",
            "monthly_fixed_expenses": 1500,
            "financial_goal": "save_more",
            "risk_tolerance": "medium",
            "budget_style": "optimize_value",
            "value_priority_cost": 70,
            "value_priority_quality": 60,
            "value_priority_sustainability": 45,
            "self_report_research_habit": 55,
        }

    @override_settings(
        SUPABASE_EMAIL_CONFIRM_CALLBACK_URI="http://127.0.0.1:8000/api/auth/email/confirm/",
        SUPABASE_EMAIL_CONFIRM_REDIRECT_TO="http://127.0.0.1:5173/onboarding",
        ONBOARDING_AFTER_COMPLETE_REDIRECT_TO="/dashboard",
    )
    @patch("users.auth_views.audit_login")
    @patch("users.auth_views.supabase_get_user")
    @patch("users.auth_views.supabase_mfa_verify")
    @patch("users.auth_views.supabase_mfa_challenge")
    @patch("users.auth_views.supabase_verify_email_token")
    @patch("users.auth_views.supabase_signup")
    def test_signup_email_verify_mfa_onboarding_full_journey(
        self,
        mock_signup,
        mock_verify_email,
        mock_mfa_challenge,
        mock_mfa_verify,
        mock_get_user,
        mock_audit,
    ):
        # 1) Signup requires email verification.
        mock_signup.return_value = {}
        signup_response = self.client.post(
            reverse("auth-signup"),
            {"email": "journey@example.com", "password": "secret"},
            format="json",
            **_csrf_header(self.client),
        )
        self.assertEqual(signup_response.status_code, status.HTTP_200_OK)
        self.assertTrue(signup_response.json()["email_confirmation_required"])
        self.assertEqual(
            signup_response.json()["email_confirmation_redirect_to"],
            "http://127.0.0.1:5173/onboarding?next=%2Fdashboard",
        )

        # 2) Email confirm callback creates pending-MFA session and redirects to onboarding.
        uid = str(uuid.uuid4())
        mock_verify_email.return_value = {
            "access_token": "confirm-at",
            "refresh_token": "confirm-rt",
            "expires_in": 3600,
            "user": {
                "id": uid,
                "email": "journey@example.com",
                "email_confirmed_at": "2020-01-01T00:00:00Z",
                "aal": "aal1",
                "factors": [],
            },
        }
        confirm_response = self.client.get(
            reverse("auth-email-confirm-callback"),
            {"token_hash": "token-hash", "type": "signup"},
            follow=False,
        )
        self.assertEqual(confirm_response.status_code, status.HTTP_302_FOUND)
        _assert_redirect_location(
            self,
            confirm_response["Location"],
            "http://127.0.0.1:5173/onboarding",
            {
                "next": "/dashboard",
                "login": "mfa_enrollment_required",
                "next_aal": "aal2",
            },
        )

        me_pending = self.client.get(reverse("auth-me"))
        self.assertEqual(me_pending.status_code, status.HTTP_200_OK)
        self.assertTrue(me_pending.json()["mfa_pending"])
        self.assertTrue(me_pending.json()["mfa_enrollment_required"])

        # 3) Onboarding is available while MFA is pending.
        onboarding_list_before = self.client.get(reverse("raw-explicit-list"))
        self.assertEqual(onboarding_list_before.status_code, status.HTTP_200_OK)
        self.assertEqual(onboarding_list_before.json(), [])

        onboarding_create = self.client.post(
            reverse("raw-explicit-list"),
            self._onboarding_payload(),
            format="json",
            **_csrf_header(self.client),
        )
        self.assertEqual(onboarding_create.status_code, status.HTTP_201_CREATED)

        onboarding_list_after = self.client.get(reverse("raw-explicit-list"))
        self.assertEqual(onboarding_list_after.status_code, status.HTTP_200_OK)
        self.assertEqual(len(onboarding_list_after.json()), 1)

        # 4) MFA enrollment verification finalizes sign-in (AAL2) without losing onboarding data.
        mock_mfa_challenge.return_value = {"id": "challenge-1"}
        mock_mfa_verify.return_value = {
            "access_token": "aal2-at",
            "refresh_token": "aal2-rt",
            "expires_in": 3600,
        }
        mock_get_user.return_value = {
            "aal": "aal2",
            "factors": [{"id": "factor-1", "status": "verified"}],
        }
        verify_response = self.client.post(
            reverse("auth-mfa-verify-enrollment"),
            {"factor_id": "factor-1", "code": "123456"},
            format="json",
            **_csrf_header(self.client),
        )
        self.assertEqual(verify_response.status_code, status.HTTP_200_OK)
        self.assertEqual(verify_response.json()["aal"], "aal2")
        self.assertFalse(verify_response.json()["mfa_pending"])
        self.assertEqual(verify_response.json()["next_step"], "dashboard")

        me_after_verify = self.client.get(reverse("auth-me"))
        self.assertEqual(me_after_verify.status_code, status.HTTP_200_OK)
        self.assertEqual(me_after_verify.json()["aal"], "aal2")
        self.assertFalse(me_after_verify.json()["mfa_pending"])
        self.assertEqual(me_after_verify.json()["next_step"], "dashboard")

        onboarding_still_present = self.client.get(reverse("raw-explicit-list"))
        self.assertEqual(onboarding_still_present.status_code, status.HTTP_200_OK)
        self.assertEqual(len(onboarding_still_present.json()), 1)


class OAuthCallbackViewTests(TestCase):
    def setUp(self):
        self.client = APIClient()

    @override_settings(SUPABASE_OAUTH_REDIRECT_URI="http://127.0.0.1:8000/api/auth/oauth/callback/")
    @patch("users.auth_views.audit_login")
    @patch("users.auth_views.supabase_exchange_pkce")
    def test_oauth_callback_redirects_to_first_login_confirmation_for_new_google_user(
        self,
        mock_exchange,
        mock_audit,
    ):
        uid = str(uuid.uuid4())
        mock_exchange.return_value = {
            "access_token": "oauth-at",
            "refresh_token": "oauth-rt",
            "expires_in": 3600,
            "user": {
                "id": uid,
                "email": "oauth@example.com",
                "email_confirmed_at": "2020-01-01T00:00:00Z",
                "aal": "aal2",
                "factors": [{"id": "factor-1", "status": "verified"}],
            },
        }

        session = self.client.session
        session["oauth_pkce"] = {
            "code_verifier": "verifier-123",
            "provider": "google",
            "frontend_redirect": "http://127.0.0.1:5173/auth/callback",
            "state": "state-123",
        }
        session.save()

        response = self.client.get(
            reverse("auth-oauth-callback"),
            {"code": "auth-code-123", "state": "state-123"},
            follow=False,
        )

        self.assertEqual(response.status_code, status.HTTP_302_FOUND)
        _assert_redirect_location(
            self,
            response["Location"],
            "http://127.0.0.1:5173/auth/callback",
            {
                "login": "oauth_first_login_confirmation_required",
                "next_aal": "aal2",
                "first_login_confirmation_required": "1",
            },
        )
        self.assertIn(settings.SESSION_COOKIE_NAME, self.client.cookies)

        me_response = self.client.get(reverse("auth-me"))
        self.assertEqual(me_response.status_code, status.HTTP_200_OK)
        self.assertEqual(me_response.json()["email"], "oauth@example.com")
        self.assertEqual(me_response.json()["aal"], "aal2")

    @override_settings(SUPABASE_OAUTH_REDIRECT_URI="http://127.0.0.1:8000/api/auth/oauth/callback/")
    @patch("users.auth_views.audit_login")
    @patch("users.auth_views.supabase_exchange_pkce")
    def test_oauth_callback_existing_google_user_redirects_with_success(
        self,
        mock_exchange,
        mock_audit,
    ):
        uid = str(uuid.uuid4())
        existing_user = User.objects.create_user(
            username="oauth-existing",
            email="oauth-existing@example.com",
            password=None,
        )
        existing_user.supabase_uid = uuid.UUID(uid)
        existing_user.save(update_fields=["supabase_uid"])
        mock_exchange.return_value = {
            "access_token": "oauth-at",
            "refresh_token": "oauth-rt",
            "expires_in": 3600,
            "user": {
                "id": uid,
                "email": "oauth-existing@example.com",
                "email_confirmed_at": "2020-01-01T00:00:00Z",
                "aal": "aal2",
                "factors": [{"id": "factor-1", "status": "verified"}],
            },
        }

        session = self.client.session
        session["oauth_pkce"] = {
            "code_verifier": "verifier-123",
            "provider": "google",
            "frontend_redirect": "http://127.0.0.1:5173/auth/callback",
            "state": "state-123",
        }
        session.save()

        response = self.client.get(
            reverse("auth-oauth-callback"),
            {"code": "auth-code-123", "state": "state-123"},
            follow=False,
        )

        self.assertEqual(response.status_code, status.HTTP_302_FOUND)
        _assert_redirect_location(
            self,
            response["Location"],
            "http://127.0.0.1:5173/auth/callback",
            {"login": "success"},
        )

    @override_settings(SUPABASE_OAUTH_REDIRECT_URI="http://127.0.0.1:8000/api/auth/oauth/callback/")
    @patch("users.auth_views.audit_login")
    @patch("users.auth_views.supabase_exchange_pkce")
    def test_oauth_callback_redirects_to_mfa_enrollment_when_pending(
        self,
        mock_exchange,
        mock_audit,
    ):
        uid = str(uuid.uuid4())
        mock_exchange.return_value = {
            "access_token": "oauth-at",
            "refresh_token": "oauth-rt",
            "expires_in": 3600,
            "user": {
                "id": uid,
                "email": "oauth@example.com",
                "email_confirmed_at": "2020-01-01T00:00:00Z",
                "aal": "aal1",
                "factors": [],
            },
        }

        session = self.client.session
        session["oauth_pkce"] = {
            "code_verifier": "verifier-123",
            "provider": "google",
            "frontend_redirect": "http://127.0.0.1:5173/auth/callback",
            "state": "state-123",
        }
        session.save()

        response = self.client.get(
            reverse("auth-oauth-callback"),
            {"code": "auth-code-123", "state": "state-123"},
            follow=False,
        )

        self.assertEqual(response.status_code, status.HTTP_302_FOUND)
        _assert_redirect_location(
            self,
            response["Location"],
            "http://127.0.0.1:5173/auth/callback",
            {
                "login": "mfa_enrollment_required",
                "next_aal": "aal2",
                "first_login_confirmation_required": "1",
            },
        )

        me_response = self.client.get(reverse("auth-me"))
        self.assertEqual(me_response.status_code, status.HTTP_200_OK)
        self.assertTrue(me_response.json()["mfa_pending"])


class LogoutViewTests(TestCase):
    def setUp(self):
        self.client = APIClient(enforce_csrf_checks=True)
        self.user = User.objects.create_user(
            username="out_u",
            email="out@example.com",
            password="x",
        )

    @patch("users.auth_views.supabase_logout")
    def test_logout_clears_session(self, mock_sb_logout):
        # CSRF first, then login + session payload so GET does not drop auth block.
        self.client.get(reverse("auth-csrf"))
        self.client.force_login(self.user)
        sess = self.client.session
        sess[AUTH_SESSION_KEY] = {
            "user_id": self.user.pk,
            "_supabase_access_token": "tok",
        }
        sess.save()
        r = self.client.post(
            reverse("auth-logout"), {}, format="json", **_csrf_header(self.client)
        )
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        mock_sb_logout.assert_called_once_with("tok")
        self.assertNotIn(AUTH_SESSION_KEY, self.client.session)


class MfaViewTests(TestCase):
    def setUp(self):
        self.client = APIClient(enforce_csrf_checks=True)
        self.user = User.objects.create_user(
            username="mfa_u",
            email="mfa@example.com",
            password="x",
        )

    def _set_session_auth(self, **overrides):
        self.client.get(reverse("auth-csrf"))
        self.client.force_login(self.user)
        sess = self.client.session
        sess[AUTH_SESSION_KEY] = {
            "user_id": self.user.pk,
            "aal": "aal1",
            "mfa_factor_count": 1,
            "_supabase_access_token": "tok",
            "_supabase_refresh_token": "rt",
            "_supabase_token_expires_at": 9999999999,
            **overrides,
        }
        sess.save()

    def _set_pending_session_auth(self, **overrides):
        self.client.get(reverse("auth-csrf"))
        sess = self.client.session
        sess[AUTH_SESSION_KEY] = {
            "user_id": self.user.pk,
            "aal": "aal1",
            "mfa_factor_count": 0,
            "mfa_pending": True,
            "next_aal": "aal2",
            "mfa_enrollment_required": True,
            "auth_method": "supabase_password",
            "_supabase_access_token": "tok",
            "_supabase_refresh_token": "rt",
            "_supabase_token_expires_at": 9999999999,
            **overrides,
        }
        sess.save()

    @patch("users.auth_views.supabase_get_user")
    def test_mfa_snapshot_returns_factor_list(self, mock_get_user):
        self._set_session_auth()
        mock_get_user.return_value = {
            "aal": "aal1",
            "factors": [
                {
                    "id": "factor-1",
                    "friendly_name": "Authenticator app",
                    "factor_type": "totp",
                    "status": "verified",
                }
            ],
        }
        response = self.client.get(reverse("auth-mfa-snapshot"))
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["current_level"], "aal1")
        self.assertEqual(data["next_level"], "aal2")
        self.assertEqual(data["factors"][0]["id"], "factor-1")

    @patch("users.auth_views.supabase_get_user")
    def test_mfa_snapshot_preserves_verified_session_for_current_browser_session(self, mock_get_user):
        self._set_session_auth(
            aal="aal2",
            mfa_pending=False,
            next_aal=None,
            mfa_enrollment_required=False,
            last_step_up_at=123,
        )
        mock_get_user.return_value = {
            "aal": "aal1",
            "factors": [
                {
                    "id": "factor-1",
                    "friendly_name": "Authenticator app",
                    "factor_type": "totp",
                    "status": "verified",
                }
            ],
        }

        response = self.client.get(reverse("auth-mfa-snapshot"))
        self.assertEqual(response.status_code, 200)

        block = self.client.session[AUTH_SESSION_KEY]
        self.assertEqual(block["aal"], "aal2")
        self.assertEqual(block["mfa_factor_count"], 1)
        self.assertEqual(block["last_step_up_at"], 123)
        self.assertFalse(block["mfa_pending"])

        me_response = self.client.get(reverse("auth-me"))
        self.assertEqual(me_response.status_code, 200)
        self.assertEqual(me_response.json()["aal"], "aal2")
        self.assertFalse(me_response.json()["mfa_pending"])

    @patch("users.auth_views.supabase_get_user")
    @patch("users.auth_views.supabase_mfa_enroll_totp")
    def test_mfa_enroll_returns_supabase_payload(self, mock_enroll, mock_get_user):
        self._set_session_auth()
        mock_enroll.return_value = {
            "id": "factor-1",
            "totp": {"qr_code": "data:image/png;base64,abc", "secret": "SECRET"},
        }
        response = self.client.post(
            reverse("auth-mfa-enroll"),
            {"friendly_name": "Authenticator app"},
            format="json",
            **_csrf_header(self.client),
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["id"], "factor-1")
        self.assertEqual(response.json()["totp"]["secret"], "SECRET")
        mock_get_user.assert_not_called()

    @patch("users.auth_views.supabase_mfa_enroll_totp")
    def test_mfa_enroll_wraps_svg_qr_code_as_data_uri(self, mock_enroll):
        self._set_session_auth()
        raw_svg = '<?xml version="1.0"?><svg xmlns="http://www.w3.org/2000/svg"></svg>'
        mock_enroll.return_value = {
            "id": "factor-1",
            "totp": {"qr_code": raw_svg, "secret": "SECRET"},
        }
        response = self.client.post(
            reverse("auth-mfa-enroll"),
            {"friendly_name": "Authenticator app"},
            format="json",
            **_csrf_header(self.client),
        )
        self.assertEqual(response.status_code, 201)
        qr_code = response.json()["totp"]["qr_code"]
        self.assertTrue(qr_code.startswith("data:image/svg+xml;base64,"))
        self.assertEqual(base64.b64decode(qr_code.split(",", 1)[1]).decode("utf-8"), raw_svg)

    @patch("users.auth_views.supabase_get_user")
    @patch("users.auth_views.supabase_mfa_verify")
    @patch("users.auth_views.supabase_mfa_challenge")
    @override_settings(
        BANKING_REQUIRE_MFA=True,
        BANKING_REQUIRE_FINANCIAL_CONSENT=False,
        BANKING_STEP_UP_REQUIRED=True,
        BANKING_STEP_UP_FRESHNESS_SECONDS=900,
    )
    def test_mfa_step_up_flow_allows_banking_action(self, mock_challenge, mock_verify, mock_get_user):
        self._set_session_auth(last_step_up_at=None)
        mock_challenge.return_value = {"id": "challenge-1"}
        mock_verify.return_value = {}
        mock_get_user.return_value = {
            "aal": "aal2",
            "factors": [
                {"id": "factor-1", "friendly_name": "Authenticator", "factor_type": "totp", "status": "verified"}
            ],
        }

        challenge_response = self.client.post(
            reverse("auth-mfa-challenge"),
            {"factor_id": "factor-1"},
            format="json",
            **_csrf_header(self.client),
        )
        self.assertEqual(challenge_response.status_code, 200)
        self.assertEqual(challenge_response.json()["challenge_id"], "challenge-1")

        verify_response = self.client.post(
            reverse("auth-mfa-verify"),
            {"factor_id": "factor-1", "challenge_id": "challenge-1", "code": "123456"},
            format="json",
            **_csrf_header(self.client),
        )
        self.assertEqual(verify_response.status_code, 200)
        self.assertEqual(verify_response.json()["aal"], "aal2")

        with patch("banking.views.create_link_token_for_user", return_value="link-tok"):
            link_response = self.client.post(
                "/api/banking/link-token/",
                {},
                format="json",
                **_csrf_header(self.client),
            )
        self.assertEqual(link_response.status_code, 200)
        self.assertEqual(link_response.json()["link_token"], "link-tok")

    def test_banking_endpoints_reject_bearer_only_auth(self):
        bearer_client = APIClient()
        bearer_client.credentials(HTTP_AUTHORIZATION="Bearer test-token")
        response = bearer_client.post(reverse("banking-link-token"), {}, format="json")
        self.assertIn(
            response.status_code,
            (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN),
        )

    @patch("users.auth_views.supabase_get_user")
    @patch("users.auth_views.supabase_mfa_unenroll")
    def test_mfa_factor_delete_updates_session_state(self, mock_unenroll, mock_get_user):
        self._set_session_auth(aal="aal2", last_step_up_at=123)
        mock_unenroll.return_value = None
        mock_get_user.return_value = {"aal": "aal1", "factors": []}
        response = self.client.delete(
            reverse("auth-mfa-factor", kwargs={"factor_id": "factor-1"}),
            **_csrf_header(self.client),
        )
        self.assertEqual(response.status_code, 204)
        block = self.client.session[AUTH_SESSION_KEY]
        self.assertEqual(block["mfa_factor_count"], 0)
        self.assertEqual(block["aal"], "aal1")
        self.assertIsNone(block["last_step_up_at"])
        self.assertTrue(block["mfa_pending"])
        self.assertTrue(block["mfa_enrollment_required"])

        me_response = self.client.get(reverse("auth-me"))
        self.assertEqual(me_response.status_code, 200)
        self.assertTrue(me_response.json()["mfa_pending"])

        sync_response = self.client.post(
            reverse("supabase-user-sync"),
            {},
            format="json",
            **_csrf_header(self.client),
        )
        self.assertEqual(sync_response.status_code, status.HTTP_200_OK)
        self.assertTrue(sync_response.json()["mfa_pending"])
        self.assertEqual(sync_response.json()["next_step"], "mfa_setup")
        self.assertEqual(sync_response.json()["post_mfa_step"], "onboarding_survey")

    @patch("users.auth_views.supabase_get_user")
    @patch("users.auth_views.supabase_mfa_verify")
    @patch("users.auth_views.supabase_mfa_challenge")
    def test_pending_enrollment_verify_promotes_session_to_full_login(
        self,
        mock_challenge,
        mock_verify,
        mock_get_user,
    ):
        self._set_pending_session_auth()
        mock_challenge.return_value = {"id": "challenge-1"}
        mock_verify.return_value = {
            "access_token": "tok-aal2",
            "refresh_token": "rt-aal2",
            "expires_in": 3600,
        }
        mock_get_user.return_value = {
            "aal": "aal2",
            "factors": [
                {
                    "id": "factor-1",
                    "friendly_name": "Authenticator",
                    "factor_type": "totp",
                    "status": "verified",
                }
            ],
        }

        response = self.client.post(
            reverse("auth-mfa-verify-enrollment"),
            {"factor_id": "factor-1", "code": "123456"},
            format="json",
            **_csrf_header(self.client),
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["aal"], "aal2")
        self.assertFalse(response.json()["mfa_pending"])
        self.assertEqual(response.json()["next_step"], "onboarding_survey")

        me_response = self.client.get(reverse("auth-me"))
        self.assertEqual(me_response.status_code, 200)
        self.assertEqual(me_response.json()["aal"], "aal2")
        self.assertFalse(me_response.json()["mfa_pending"])
        self.assertEqual(me_response.json()["next_step"], "onboarding_survey")

        sync_response = self.client.post(
            reverse("supabase-user-sync"),
            {},
            format="json",
            **_csrf_header(self.client),
        )
        self.assertEqual(sync_response.status_code, 200)


class ExtractAssuranceFromRequestTests(TestCase):
    def test_prefers_session_over_jwt(self):
        factory = APIRequestFactory()
        request = factory.get("/")
        _add_session_to_request(request)
        request.session[AUTH_SESSION_KEY] = {
            "user_id": 1,
            "aal": "aal2",
            "mfa_factor_count": 2,
        }
        request.session.save()
        request.auth = {"aal": "aal1", "mfa_factors_count": 0, "amr": []}
        info = extract_assurance_from_request(request)
        self.assertEqual(info["aal"], "aal2")
        self.assertEqual(info["_assurance_source"], "session")


class SupabaseUserSyncJwtCompatTests(TestCase):
    """Legacy sync endpoint still works with JWT-style request.auth at AAL2."""

    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(
            username="sync_u",
            email="sync@example.com",
            password="x",
        )

    def test_sync_returns_user_payload(self):
        self.client.force_authenticate(
            user=self.user,
            token={"aal": "aal2", "mfa_factors_count": 1},
        )
        response = self.client.post(reverse("supabase-user-sync"), {}, format="json")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["email"], "sync@example.com")
