"""
Regression tests for the auth hardening work:

* Supabase bearer tokens are no longer a request-authentication path
  (session cookies are the sole API auth method).
* A pending-MFA session can still drive the MFA step-up flow (it can read its
  own auth state) but must not reach financial or profile data -- AAL2 is
  enforced both at the authentication layer (plain SessionAuthentication does
  not resolve a pending-MFA session) and by the IsFullyAuthenticated permission
  on views that opt into AuthSessionAuthentication.
"""
from __future__ import annotations

from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from apps.users.session_auth import AUTH_SESSION_KEY
from apps.users.permissions import IsFullyAuthenticated

User = get_user_model()

_FULL_BLOCK = {
    "aal": "aal2",
    "mfa_factor_count": 1,
    "mfa_pending": False,
    "_supabase_access_token": "tok",
    "_supabase_refresh_token": "rt",
    "_supabase_token_expires_at": 9999999999,
}

_PENDING_BLOCK = {
    "aal": "aal1",
    "mfa_factor_count": 0,
    "mfa_pending": True,
    "next_aal": "aal2",
    "mfa_enrollment_required": True,
    "auth_method": "supabase_password",
    "_supabase_access_token": "tok",
    "_supabase_refresh_token": "rt",
    "_supabase_token_expires_at": 9999999999,
}


class DefaultAuthenticationClassesTests(TestCase):
    def test_jwt_authentication_is_not_a_default_request_auth_class(self):
        defaults = tuple(
            settings.REST_FRAMEWORK.get("DEFAULT_AUTHENTICATION_CLASSES", ())
        )
        self.assertNotIn(
            "apps.users.supabase_auth.SupabaseJWTAuthentication",
            defaults,
            "Supabase JWT must not be a default request-authentication method.",
        )
        self.assertIn(
            "rest_framework.authentication.SessionAuthentication",
            defaults,
        )


class BearerTokenRejectionTests(TestCase):
    """A bearer token must not authenticate any protected endpoint."""

    def setUp(self):
        self.client = APIClient()
        self.client.credentials(HTTP_AUTHORIZATION="Bearer any-supabase-looking-token")

    def test_bearer_rejected_on_me(self):
        resp = self.client.get(reverse("auth-me"))
        self.assertIn(
            resp.status_code,
            (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN),
        )

    def test_bearer_rejected_on_transactions(self):
        resp = self.client.get(reverse("transactions-list"))
        self.assertIn(
            resp.status_code,
            (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN),
        )


class MfaGatingTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="gated_u", email="gated@example.com", password="pw"
        )

    def _client_with_block(self, block, *, full_login: bool) -> APIClient:
        client = APIClient()
        if full_login:
            client.force_login(self.user)
        sess = client.session
        sess[AUTH_SESSION_KEY] = {"user_id": self.user.pk, **block}
        sess.save()
        return client

    # --- pending MFA session ----------------------------------------------
    def test_pending_session_can_read_auth_state(self):
        """The frontend polls /me/ mid-step-up to learn the next step."""
        client = self._client_with_block(_PENDING_BLOCK, full_login=False)
        resp = client.get(reverse("auth-me"))
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertTrue(resp.data["mfa_pending"])

    def test_pending_session_can_reach_onboarding_profile_data(self):
        """
        Onboarding/profile data is part of the pre-MFA signup journey, so a
        pending-MFA session must still reach it (it uses AuthSessionAuthentication
        + IsAuthenticated, not IsFullyAuthenticated).
        """
        client = self._client_with_block(_PENDING_BLOCK, full_login=False)
        resp = client.get(reverse("raw-explicit-list"))
        self.assertEqual(resp.status_code, status.HTTP_200_OK)

    def test_pending_session_blocked_from_financial_data(self):
        """Plain SessionAuthentication never resolves a pending-MFA session."""
        client = self._client_with_block(_PENDING_BLOCK, full_login=False)
        resp = client.get(reverse("transactions-list"))
        self.assertIn(
            resp.status_code,
            (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN),
        )

    # --- fully authenticated session --------------------------------------
    def test_full_session_reaches_financial_data(self):
        client = self._client_with_block(_FULL_BLOCK, full_login=True)
        resp = client.get(reverse("transactions-list"))
        self.assertEqual(resp.status_code, status.HTTP_200_OK)

    def test_full_session_reaches_profile_data(self):
        client = self._client_with_block(_FULL_BLOCK, full_login=True)
        resp = client.get(reverse("preferences-list"))
        self.assertEqual(resp.status_code, status.HTTP_200_OK)


class MeNameCapTests(TestCase):
    """MeView.patch caps the supplied name length (L-3 input hardening)."""

    def setUp(self):
        self.user = User.objects.create_user(
            username="namer", email="n@example.com", password="pw"
        )
        self.client = APIClient()
        self.client.force_login(self.user)

    def test_overlong_name_is_truncated(self):
        resp = self.client.patch(
            reverse("auth-me"), {"name": "x" * 500}, format="json"
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.user.refresh_from_db()
        self.assertEqual(len(self.user.first_name), 120)


class IsFullyAuthenticatedUnitTests(TestCase):
    """Direct coverage of the defense-in-depth MFA permission guard."""

    def _request(self, block, user):
        from django.contrib.sessions.middleware import SessionMiddleware
        from rest_framework.test import APIRequestFactory

        request = APIRequestFactory().get("/")
        SessionMiddleware(lambda r: None).process_request(request)
        if block is not None:
            request.session[AUTH_SESSION_KEY] = block
            request.session.save()
        request.user = user
        return request

    def test_rejects_pending_mfa_session(self):
        user = User.objects.create_user(username="p1", password="pw")
        req = self._request({"user_id": user.pk, "mfa_pending": True}, user)
        self.assertFalse(IsFullyAuthenticated().has_permission(req, None))

    def test_allows_full_session(self):
        user = User.objects.create_user(username="p2", password="pw")
        req = self._request({"user_id": user.pk, "mfa_pending": False}, user)
        self.assertTrue(IsFullyAuthenticated().has_permission(req, None))

    def test_allows_session_without_auth_block(self):
        user = User.objects.create_user(username="p3", password="pw")
        req = self._request(None, user)
        self.assertTrue(IsFullyAuthenticated().has_permission(req, None))

    def test_rejects_anonymous(self):
        from django.contrib.auth.models import AnonymousUser

        req = self._request(None, AnonymousUser())
        self.assertFalse(IsFullyAuthenticated().has_permission(req, None))
