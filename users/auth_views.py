"""
Session-based auth API (BFF). Browser uses cookies + CSRF; no Supabase tokens in JSON.
"""
from __future__ import annotations

import logging
import secrets

from django.conf import settings
from django.http import HttpResponseRedirect
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import ensure_csrf_cookie
from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .session_auth import (
    SupabaseAuthError,
    audit_login,
    audit_logout,
    audit_mfa_verify,
    build_mfa_snapshot_payload,
    build_oauth_authorize_url,
    clear_session,
    extract_session_data,
    get_or_create_local_user,
    get_session_auth_state,
    get_supabase_access_token,
    issue_django_session,
    normalize_mfa_enroll_payload,
    pop_oauth_pkce_state,
    pkce_challenge,
    pkce_verifier,
    record_step_up,
    refresh_session_tokens_if_needed,
    store_oauth_pkce_state,
    supabase_exchange_pkce,
    supabase_get_user,
    supabase_login,
    supabase_logout,
    supabase_mfa_enroll_totp,
    supabase_mfa_challenge,
    supabase_mfa_unenroll,
    supabase_mfa_verify,
    supabase_signup,
    sync_session_assurance_from_user,
    user_from_token_response,
)

logger = logging.getLogger(__name__)


@method_decorator(ensure_csrf_cookie, name="dispatch")
class CsrfView(APIView):
    """GET: set csrftoken cookie for subsequent unsafe requests."""

    permission_classes = [AllowAny]
    authentication_classes = []

    def get(self, request):
        return Response({"detail": "CSRF cookie set"})


class LoginView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        email = (request.data.get("email") or "").strip()
        password = request.data.get("password") or ""
        if not email or not password:
            return Response(
                {"detail": "email and password are required", "error_code": "validation_error"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            data = supabase_login(email, password)
        except SupabaseAuthError as exc:
            audit_login(request, None, "failure", exc.error_code)
            return Response(
                {"detail": exc.message, "error_code": exc.error_code},
                status=status.HTTP_400_BAD_REQUEST,
            )
        access = data.get("access_token")
        if not access:
            audit_login(request, None, "failure", "no_access_token")
            return Response(
                {"detail": "Login did not return a session.", "error_code": "no_access_token"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            supabase_uid, canonical_email = user_from_token_response(data)
        except SupabaseAuthError as exc:
            audit_login(request, None, "failure", exc.error_code)
            return Response(
                {"detail": exc.message, "error_code": exc.error_code},
                status=status.HTTP_403_FORBIDDEN,
            )
        try:
            user = get_or_create_local_user(supabase_uid, canonical_email)
        except SupabaseAuthError as exc:
            audit_login(request, None, "failure", exc.error_code)
            return Response(
                {"detail": exc.message, "error_code": exc.error_code},
                status=status.HTTP_400_BAD_REQUEST,
            )

        session_bits = extract_session_data(data)
        issue_django_session(
            request,
            user,
            session_bits,
            access,
            data.get("refresh_token"),
            data.get("expires_in"),
        )
        audit_login(request, user, "success", auth_method="supabase_password")
        return Response(
            {
                "user": {
                    "id": user.id,
                    "email": user.email,
                    "username": user.username,
                    "supabase_uid": str(user.supabase_uid) if user.supabase_uid else None,
                },
                "aal": session_bits.get("aal"),
                "mfa_factor_count": session_bits.get("mfa_factor_count", -1),
            },
            status=status.HTTP_200_OK,
        )


class SignupView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        email = (request.data.get("email") or "").strip()
        password = request.data.get("password") or ""
        if not email or not password:
            return Response(
                {"detail": "email and password are required", "error_code": "validation_error"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            data = supabase_signup(email, password)
        except SupabaseAuthError as exc:
            return Response(
                {"detail": exc.message, "error_code": exc.error_code},
                status=status.HTTP_400_BAD_REQUEST,
            )
        access = data.get("access_token")
        if not access:
            return Response({"email_confirmation_required": True}, status=status.HTTP_200_OK)
        try:
            supabase_uid, canonical_email = user_from_token_response(data)
        except SupabaseAuthError as exc:
            return Response(
                {"detail": exc.message, "error_code": exc.error_code},
                status=status.HTTP_403_FORBIDDEN,
            )
        try:
            user = get_or_create_local_user(supabase_uid, canonical_email)
        except SupabaseAuthError as exc:
            return Response(
                {"detail": exc.message, "error_code": exc.error_code},
                status=status.HTTP_400_BAD_REQUEST,
            )
        session_bits = extract_session_data(data)
        issue_django_session(
            request,
            user,
            session_bits,
            access,
            data.get("refresh_token"),
            data.get("expires_in"),
        )
        audit_login(request, user, "success", auth_method="supabase_signup")
        return Response(
            {
                "user": {
                    "id": user.id,
                    "email": user.email,
                    "username": user.username,
                    "supabase_uid": str(user.supabase_uid) if user.supabase_uid else None,
                },
                "aal": session_bits.get("aal"),
                "mfa_factor_count": session_bits.get("mfa_factor_count", -1),
            },
            status=status.HTTP_200_OK,
        )


class LogoutView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        user = request.user if request.user.is_authenticated else None
        token = clear_session(request)
        if token:
            supabase_logout(token)
        if user:
            audit_logout(request, user)
        resp = Response({"detail": "Logged out"}, status=status.HTTP_200_OK)
        resp.delete_cookie(
            settings.SESSION_COOKIE_NAME,
            path=settings.SESSION_COOKIE_PATH,
            domain=getattr(settings, "SESSION_COOKIE_DOMAIN", None) or None,
            samesite=getattr(settings, "SESSION_COOKIE_SAMESITE", None),
        )
        return resp


class MeView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        refresh_session_tokens_if_needed(request)
        state = get_session_auth_state(request)
        if state:
            return Response(
                {
                    "id": request.user.id,
                    "email": request.user.email,
                    "username": request.user.username,
                    "supabase_uid": str(request.user.supabase_uid)
                    if request.user.supabase_uid
                    else None,
                    "aal": state.get("aal"),
                    "mfa_factor_count": state.get("mfa_factor_count", -1),
                    "last_step_up_at": state.get("last_step_up_at"),
                    "logged_in_at": state.get("logged_in_at"),
                }
            )
        return Response(
            {
                "id": request.user.id,
                "email": request.user.email,
                "username": request.user.username,
                "supabase_uid": str(request.user.supabase_uid)
                if request.user.supabase_uid
                else None,
                "aal": None,
                "mfa_factor_count": -1,
                "last_step_up_at": None,
                "logged_in_at": None,
            }
        )


class OAuthStartView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        provider = (request.data.get("provider") or "").strip()
        if not provider:
            return Response(
                {"detail": "provider is required", "error_code": "validation_error"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        redirect_uri = (
            getattr(settings, "SUPABASE_OAUTH_REDIRECT_URI", None) or ""
        ).strip()
        if not redirect_uri:
            return Response(
                {
                    "detail": "SUPABASE_OAUTH_REDIRECT_URI is not configured.",
                    "error_code": "oauth_not_configured",
                },
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        frontend_redirect = (request.data.get("redirect_uri_after") or "").strip() or None
        verifier = pkce_verifier()
        challenge = pkce_challenge(verifier)
        store_oauth_pkce_state(
            request,
            code_verifier=verifier,
            provider=provider,
            frontend_redirect=frontend_redirect,
        )
        authorize_url = build_oauth_authorize_url(
            provider=provider,
            redirect_to=redirect_uri,
            code_challenge=challenge,
        )
        print("OAUTH START URL:", authorize_url)
        return Response({"authorize_url": authorize_url})


class OAuthCallbackView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []

    def get(self, request):
        code = request.query_params.get("code")
        err = request.query_params.get("error_description") or request.query_params.get("error")
        if err:
            return Response(
                {"detail": err, "error_code": "oauth_provider_error"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if not code:
            return Response(
                {"detail": "Missing code.", "error_code": "oauth_invalid_callback"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        pkce_state = pop_oauth_pkce_state(request)
        if not pkce_state:
            return Response(
                {"detail": "Missing PKCE session.", "error_code": "oauth_missing_session"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        verifier = (pkce_state.get("code_verifier") or "").strip()
        if not verifier:
            return Response(
                {"detail": "Missing code verifier.", "error_code": "oauth_missing_verifier"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        frontend = (pkce_state.get("frontend_redirect") or "").strip()
        try:
            data = supabase_exchange_pkce(code, verifier)
        except SupabaseAuthError as exc:
            return Response(
                {"detail": exc.message, "error_code": exc.error_code},
                status=status.HTTP_400_BAD_REQUEST,
            )
        access = data.get("access_token")
        if not access:
            return Response(
                {"detail": "OAuth exchange did not return tokens.", "error_code": "oauth_no_tokens"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            supabase_uid, canonical_email = user_from_token_response(data)
        except SupabaseAuthError as exc:
            return Response(
                {"detail": exc.message, "error_code": exc.error_code},
                status=status.HTTP_403_FORBIDDEN,
            )
        try:
            user = get_or_create_local_user(supabase_uid, canonical_email)
        except SupabaseAuthError as exc:
            return Response(
                {"detail": exc.message, "error_code": exc.error_code},
                status=status.HTTP_400_BAD_REQUEST,
            )
        session_bits = extract_session_data(data)
        session_bits["auth_method"] = "oauth"
        issue_django_session(
            request,
            user,
            session_bits,
            access,
            data.get("refresh_token"),
            data.get("expires_in"),
        )
        provider = (pkce_state.get("provider") or "oauth").strip().lower()
        audit_login(request, user, "success", auth_method=f"oauth_{provider}")
        if frontend:
            sep = "&" if "?" in frontend else "?"
            target = f"{frontend}{sep}login=success"
            return HttpResponseRedirect(target)
        return Response(
            {
                "user": {
                    "id": user.id,
                    "email": user.email,
                    "username": user.username,
                    "supabase_uid": str(user.supabase_uid) if user.supabase_uid else None,
                },
                "aal": session_bits.get("aal"),
                "mfa_factor_count": session_bits.get("mfa_factor_count", -1),
            }
        )


class MfaChallengeView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        factor_id = (request.data.get("factor_id") or "").strip()
        if not factor_id:
            return Response(
                {"detail": "factor_id is required", "error_code": "validation_error"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        refresh_session_tokens_if_needed(request)
        access = get_supabase_access_token(request)
        if not access:
            return Response(
                {"detail": "No Supabase session in server session.", "error_code": "no_upstream_session"},
                status=status.HTTP_401_UNAUTHORIZED,
            )
        try:
            out = supabase_mfa_challenge(access, factor_id)
        except SupabaseAuthError as exc:
            return Response(
                {"detail": exc.message, "error_code": exc.error_code},
                status=status.HTTP_400_BAD_REQUEST,
            )
        challenge_id = out.get("id") or out.get("challenge_id")
        expires_at = out.get("expires_at")
        return Response(
            {
                "challenge_id": str(challenge_id) if challenge_id else "",
                "expires_at": expires_at,
            }
        )


class MfaSnapshotView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        refresh_session_tokens_if_needed(request)
        access = get_supabase_access_token(request)
        if not access:
            return Response(
                {"detail": "No Supabase session in server session.", "error_code": "no_upstream_session"},
                status=status.HTTP_401_UNAUTHORIZED,
            )
        try:
            user_obj = supabase_get_user(access)
        except SupabaseAuthError as exc:
            return Response(
                {"detail": exc.message, "error_code": exc.error_code},
                status=status.HTTP_400_BAD_REQUEST,
            )
        sync_session_assurance_from_user(request, user_obj)
        return Response(build_mfa_snapshot_payload(user_obj))


class MfaEnrollView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        friendly_name = (request.data.get("friendly_name") or "Authenticator app").strip()
        refresh_session_tokens_if_needed(request)
        access = get_supabase_access_token(request)
        if not access:
            return Response(
                {"detail": "No Supabase session in server session.", "error_code": "no_upstream_session"},
                status=status.HTTP_401_UNAUTHORIZED,
            )
        try:
            out = supabase_mfa_enroll_totp(access, friendly_name)
        except SupabaseAuthError as exc:
            return Response(
                {"detail": exc.message, "error_code": exc.error_code},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return Response(normalize_mfa_enroll_payload(out), status=status.HTTP_201_CREATED)


class MfaVerifyEnrollmentView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        factor_id = (request.data.get("factor_id") or "").strip()
        code = (request.data.get("code") or "").strip()
        if not factor_id or not code:
            return Response(
                {"detail": "factor_id and code are required", "error_code": "validation_error"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        refresh_session_tokens_if_needed(request)
        access = get_supabase_access_token(request)
        if not access:
            return Response(
                {"detail": "No Supabase session in server session.", "error_code": "no_upstream_session"},
                status=status.HTTP_401_UNAUTHORIZED,
            )
        try:
            challenge = supabase_mfa_challenge(access, factor_id)
            challenge_id = str(challenge.get("id") or challenge.get("challenge_id") or "").strip()
            if not challenge_id:
                raise SupabaseAuthError("Challenge did not return an id.", error_code="mfa_challenge_failed")
            supabase_mfa_verify(access, factor_id, challenge_id, code)
            user_obj = supabase_get_user(access)
        except SupabaseAuthError as exc:
            audit_mfa_verify(request, request.user, "failure", exc.error_code)
            return Response(
                {"detail": exc.message, "error_code": exc.error_code},
                status=status.HTTP_400_BAD_REQUEST,
            )
        record_step_up(request)
        sync_session_assurance_from_user(request, user_obj)
        state = get_session_auth_state(request) or {}
        audit_mfa_verify(request, request.user, "success")
        return Response({"aal": "aal2", "last_step_up_at": state.get("last_step_up_at")})


class MfaVerifyView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        factor_id = (request.data.get("factor_id") or "").strip()
        challenge_id = (request.data.get("challenge_id") or "").strip()
        code = (request.data.get("code") or "").strip()
        if not factor_id or not challenge_id or not code:
            return Response(
                {
                    "detail": "factor_id, challenge_id, and code are required",
                    "error_code": "validation_error",
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        refresh_session_tokens_if_needed(request)
        access = get_supabase_access_token(request)
        if not access:
            return Response(
                {"detail": "No Supabase session in server session.", "error_code": "no_upstream_session"},
                status=status.HTTP_401_UNAUTHORIZED,
            )
        try:
            supabase_mfa_verify(access, factor_id, challenge_id, code)
            user_obj = supabase_get_user(access)
        except SupabaseAuthError as exc:
            audit_mfa_verify(request, request.user, "failure", exc.error_code)
            return Response(
                {"detail": exc.message, "error_code": exc.error_code},
                status=status.HTTP_400_BAD_REQUEST,
            )
        record_step_up(request)
        sync_session_assurance_from_user(request, user_obj)
        state = get_session_auth_state(request) or {}
        last = state.get("last_step_up_at")
        audit_mfa_verify(request, request.user, "success")
        return Response({"aal": "aal2", "last_step_up_at": last})


class MfaFactorView(APIView):
    permission_classes = [IsAuthenticated]

    def delete(self, request, factor_id: str):
        factor_id = (factor_id or "").strip()
        if not factor_id:
            return Response(
                {"detail": "factor_id is required", "error_code": "validation_error"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        refresh_session_tokens_if_needed(request)
        access = get_supabase_access_token(request)
        if not access:
            return Response(
                {"detail": "No Supabase session in server session.", "error_code": "no_upstream_session"},
                status=status.HTTP_401_UNAUTHORIZED,
            )
        try:
            supabase_mfa_unenroll(access, factor_id)
            user_obj = supabase_get_user(access)
        except SupabaseAuthError as exc:
            return Response(
                {"detail": exc.message, "error_code": exc.error_code},
                status=status.HTTP_400_BAD_REQUEST,
            )
        sync_session_assurance_from_user(request, user_obj)
        return Response(status=status.HTTP_204_NO_CONTENT)


class AssuranceView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        from .security_assurance import build_assurance_payload

        return Response(build_assurance_payload(request))
