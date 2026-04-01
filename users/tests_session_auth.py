"""Tests for session BFF auth service and API views."""
from __future__ import annotations

import uuid
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient, APIRequestFactory

from banking.exceptions import MfaVerificationNeededException
from banking.security_checks import enforce_banking_policies
from users.security_assurance import banking_step_up_fresh, extract_assurance_from_request
from users.session_auth import (
    AUTH_SESSION_KEY,
    SupabaseAuthError,
    get_or_create_local_user,
    issue_django_session,
)
User = get_user_model()


def _add_session_to_request(request):
    from django.contrib.sessions.middleware import SessionMiddleware

    middleware = SessionMiddleware(lambda req: None)
    middleware.process_request(request)
    request.session.save()


def _csrf_header(client):
    token = client.cookies.get("csrftoken")
    return {"HTTP_X_CSRFTOKEN": token.value} if token else {}


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
    def test_login_success(self, mock_audit, mock_login):
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
        self.assertNotIn("access_token", r.json())

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
    """Legacy sync endpoint still works with JWT-style request.auth."""

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
            token={"aal": "aal1", "mfa_factors_count": 0},
        )
        response = self.client.post(reverse("supabase-user-sync"), {}, format="json")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["email"], "sync@example.com")
