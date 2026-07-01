"""
Session-based auth API (BFF). Browser uses cookies + CSRF; no Supabase tokens in JSON.
"""
from __future__ import annotations

import hashlib
import logging
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from django.conf import settings
from django.http import HttpResponseRedirect
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import ensure_csrf_cookie
from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import UserRawExplicit
from .session_auth import (
    SupabaseAuthError,
    auth_requires_aal2,
    audit_login,
    audit_logout,
    audit_mfa_verify,
    build_mfa_snapshot_payload,
    build_oauth_authorize_url,
    clear_session,
    extract_session_data,
    get_or_create_local_user,
    get_or_create_local_user_with_status,
    get_session_auth_state,
    get_supabase_access_token,
    get_supabase_refresh_token,
    issue_django_session,
    issue_pending_mfa_session,
    normalize_mfa_enroll_payload,
    pop_oauth_pkce_state,
    pkce_challenge,
    pkce_verifier,
    record_step_up,
    refresh_session_tokens_if_needed,
    session_requires_pending_mfa,
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
    supabase_verify_email_token,
    sync_session_assurance_from_user,
    update_supabase_tokens,
    user_from_token_response,
)
from .session_authentication import AuthSessionAuthentication
from .throttles import LoginRateThrottle, SignupRateThrottle

logger = logging.getLogger(__name__)


def _token_fingerprint(token: str) -> str:
    """SHA-256 prefix (16 hex chars) for log correlation; never logs the raw secret."""
    if not token:
        return "none"
    return hashlib.sha256(token.encode("utf-8")).hexdigest()[:16]


def _with_query_param(url: str, key: str, value: str) -> str:
    if not value:
        return url
    parsed = urlparse(url)
    params = dict(parse_qsl(parsed.query, keep_blank_values=True))
    params.setdefault(key, value)
    return urlunparse(parsed._replace(query=urlencode(params)))


def _email_confirmation_frontend_target() -> str | None:
    onboarding_url = (
        getattr(settings, "SUPABASE_EMAIL_CONFIRM_REDIRECT_TO", None) or ""
    ).strip()
    if not onboarding_url:
        return None
    dashboard_url = (
        getattr(settings, "ONBOARDING_AFTER_COMPLETE_REDIRECT_TO", None) or ""
    ).strip()
    return _with_query_param(onboarding_url, "next", dashboard_url) if dashboard_url else onboarding_url


def _email_confirmation_signup_target() -> str | None:
    callback_url = (
        getattr(settings, "SUPABASE_EMAIL_CONFIRM_CALLBACK_URI", None) or ""
    ).strip()
    if callback_url:
        return callback_url
    return _email_confirmation_frontend_target()


def _user_payload(user) -> dict[str, object]:
    return {
        "id": user.id,
        "email": user.email,
        "username": user.username,
        "supabase_uid": str(user.supabase_uid) if user.supabase_uid else None,
    }


def _has_completed_onboarding(user) -> bool:
    if not getattr(user, "pk", None):
        return False
    return UserRawExplicit.objects.filter(user_id=user.pk).exists()


def _resolve_next_steps(
    state: dict[str, object],
    *,
    onboarding_completed: bool,
) -> tuple[str, str | None]:
    post_login_step = "dashboard" if onboarding_completed else "onboarding_survey"
    if bool(state.get("mfa_pending")):
        try:
            factor_count = int(state.get("mfa_factor_count", -1))
        except (TypeError, ValueError):
            factor_count = -1
        if factor_count == 0 or bool(state.get("mfa_enrollment_required")):
            return "mfa_setup", post_login_step
        if factor_count > 0:
            return "mfa_verify", post_login_step
        return "mfa_verify", post_login_step
    return post_login_step, None


def _auth_state_payload(
    user,
    state: dict[str, object],
    *,
    detail: str | None = None,
    error_code: str | None = None,
) -> dict[str, object]:
    onboarding_completed = _has_completed_onboarding(user)
    next_step, post_mfa_step = _resolve_next_steps(
        state,
        onboarding_completed=onboarding_completed,
    )
    payload: dict[str, object] = {
        "user": _user_payload(user),
        "aal": state.get("aal"),
        "mfa_factor_count": state.get("mfa_factor_count", -1),
        "mfa_pending": bool(state.get("mfa_pending")),
        "next_aal": state.get("next_aal"),
        "mfa_enrollment_required": bool(state.get("mfa_enrollment_required")),
        "onboarding_completed": onboarding_completed,
        "onboarding_required": not onboarding_completed,
        "next_step": next_step,
        "post_mfa_step": post_mfa_step,
    }
    if detail:
        payload["detail"] = detail
    if error_code:
        payload["error_code"] = error_code
    return payload


def _resolve_display_name(user) -> str:
    first_name = (getattr(user, "first_name", "") or "").strip()
    if first_name:
        return first_name
    explicit = getattr(user, "raw_explicit", None)
    explicit_name = getattr(explicit, "display_name", "") or ""
    normalized = explicit_name.strip()
    if normalized:
        return normalized
    return user.username


def _me_payload(request, state: dict[str, object] | None) -> dict[str, object]:
    onboarding_completed = _has_completed_onboarding(request.user)
    next_step, post_mfa_step = _resolve_next_steps(
        state or {},
        onboarding_completed=onboarding_completed,
    )
    return {
        "id": request.user.id,
        "email": request.user.email,
        "username": request.user.username,
        "name": _resolve_display_name(request.user),
        "supabase_uid": str(request.user.supabase_uid)
        if request.user.supabase_uid
        else None,
        "aal": state.get("aal") if state else None,
        "mfa_factor_count": state.get("mfa_factor_count", -1) if state else -1,
        "last_step_up_at": state.get("last_step_up_at") if state else None,
        "logged_in_at": state.get("logged_in_at") if state else None,
        "mfa_pending": bool(state.get("mfa_pending")) if state else False,
        "next_aal": state.get("next_aal") if state else None,
        "mfa_enrollment_required": bool(state.get("mfa_enrollment_required")) if state else False,
        "onboarding_completed": onboarding_completed,
        "onboarding_required": not onboarding_completed,
        "next_step": next_step,
        "post_mfa_step": post_mfa_step,
    }


def _resolve_session_bits(access_token: str, data: dict) -> dict[str, object]:
    session_bits = extract_session_data(data)
    if not auth_requires_aal2() or not access_token:
        return session_bits

    factor_count = session_bits.get("mfa_factor_count", -1)
    try:
        factor_count_int = int(factor_count)
    except (TypeError, ValueError):
        factor_count_int = -1
    if factor_count_int >= 0:
        return session_bits

    try:
        user_obj = supabase_get_user(access_token)
    except SupabaseAuthError:
        return session_bits
    return extract_session_data({"user": user_obj})


def _safe_frontend_redirect(candidate: str | None) -> str | None:
    """
    Open-redirect guard for user-supplied post-auth redirect targets.

    Returns the candidate only if it is a same-origin relative path or its
    origin is explicitly allowlisted (FRONTEND_ALLOWED_REDIRECT_ORIGINS, falling
    back to CORS_ALLOWED_ORIGINS). Returns None for anything else, including
    protocol-relative ("//host") and backslash-obfuscated targets.
    """
    candidate = (candidate or "").strip()
    if not candidate:
        return None
    if candidate.startswith("//") or "\\" in candidate:
        return None
    parsed = urlparse(candidate)
    if not parsed.scheme and not parsed.netloc:
        # Same-origin relative path, e.g. "/onboarding?next=/dashboard".
        return candidate if candidate.startswith("/") else None
    allowed = (
        getattr(settings, "FRONTEND_ALLOWED_REDIRECT_ORIGINS", None)
        or getattr(settings, "CORS_ALLOWED_ORIGINS", [])
    )
    origin = f"{parsed.scheme}://{parsed.netloc}"
    return candidate if origin in allowed else None


def _auth_redirect(
    frontend: str,
    login_state: str,
    *,
    extra_params: dict[str, object] | None = None,
) -> HttpResponseRedirect:
    parsed = urlparse(frontend)
    params = dict(parse_qsl(parsed.query, keep_blank_values=True))
    params["login"] = login_state
    if login_state != "success":
        params.setdefault("next_aal", "aal2")
    if extra_params:
        for key, value in extra_params.items():
            if value is None:
                continue
            params[str(key)] = str(value)
    target = urlunparse(parsed._replace(query=urlencode(params)))
    return HttpResponseRedirect(target)


def _finish_primary_auth(request, user, data: dict, *, auth_method: str):
    access = data.get("access_token") or ""
    refresh_token = data.get("refresh_token")
    expires_in = data.get("expires_in")
    session_bits = _resolve_session_bits(access, data)
    session_bits["auth_method"] = auth_method

    pending_mfa, enrollment_required = session_requires_pending_mfa(
        session_bits.get("aal"),
        session_bits.get("mfa_factor_count", -1),
    )
    if pending_mfa:
        issue_pending_mfa_session(
            request,
            user,
            session_bits,
            access,
            refresh_token,
            expires_in,
            mfa_enrollment_required=enrollment_required,
        )
        audit_login(request, user, "success", auth_method=f"{auth_method}_pending_mfa")
        state = get_session_auth_state(request) or {}
        error_code = "mfa_enrollment_required" if enrollment_required else "mfa_required"
        detail = (
            "Set up MFA to finish signing in."
            if enrollment_required
            else "Enter your authenticator code to finish signing in."
        )
        return _auth_state_payload(user, state, detail=detail, error_code=error_code)

    issue_django_session(
        request,
        user,
        session_bits,
        access,
        refresh_token,
        expires_in,
    )
    audit_login(request, user, "success", auth_method=auth_method)
    state = get_session_auth_state(request) or session_bits
    return _auth_state_payload(user, state)


def _complete_mfa_verification(request, *, user_obj: dict[str, object], verify_out: dict[str, object]):
    state_before = get_session_auth_state(request) or {}
    pending_mfa = bool(state_before.get("mfa_pending"))
    aal_before = state_before.get("aal")
    prior_access = get_supabase_access_token(request) or ""
    access_after = str(verify_out.get("access_token") or prior_access or "").strip()
    refresh_after = verify_out.get("refresh_token")
    if refresh_after is None:
        refresh_after = get_supabase_refresh_token(request)
    expires_in = verify_out.get("expires_in")

    new_access_in_verify_out = bool(verify_out.get("access_token"))
    fp_old = _token_fingerprint(prior_access)
    fp_new = _token_fingerprint(access_after)
    tokens_replaced = new_access_in_verify_out and fp_new != fp_old
    if access_after and not new_access_in_verify_out:
        logger.warning(
            "[mfa_verify] tokens_fallback=True user_id=%s access_fp=%s "
            "(verify_out missing access_token; using session token)",
            getattr(request.user, "pk", None),
            fp_new,
        )

    if pending_mfa:
        session_bits = extract_session_data({"user": user_obj})
        # /auth/v1/user does not include aal; JWT is aal2 after successful verify.
        session_bits["aal"] = "aal2"
        session_bits["auth_method"] = state_before.get("auth_method") or "password"
        issue_django_session(
            request,
            request.user,
            session_bits,
            access_after,
            refresh_after,
            expires_in,
        )
        state = get_session_auth_state(request) or {}
        logger.info(
            "[mfa_verify] path=pending_mfa user_id=%s aal_before=%s aal_after=%s "
            "last_step_up_at=%s tokens_replaced=%s access_fp_old=%s access_fp_new=%s",
            getattr(request.user, "pk", None),
            aal_before,
            state.get("aal"),
            state.get("last_step_up_at"),
            tokens_replaced,
            fp_old,
            fp_new,
        )
        return _auth_state_payload(request.user, state)

    # is not None binds tighter than `or`; successful verify always includes access_token.
    if (
        verify_out.get("access_token")
        or verify_out.get("refresh_token") is not None
        or expires_in is not None
    ):
        update_supabase_tokens(
            request,
            access_token=access_after,
            refresh_token=refresh_after,
            expires_in=expires_in,
        )
    record_step_up(request)
    sync_session_assurance_from_user(request, user_obj)
    state = get_session_auth_state(request) or {}
    logger.info(
        "[mfa_verify] path=step_up user_id=%s aal_before=%s aal_after=%s "
        "last_step_up_at=%s tokens_replaced=%s access_fp_old=%s access_fp_new=%s",
        getattr(request.user, "pk", None),
        aal_before,
        state.get("aal"),
        state.get("last_step_up_at"),
        tokens_replaced,
        fp_old,
        fp_new,
    )
    return {
        "aal": "aal2",
        "last_step_up_at": state.get("last_step_up_at"),
        "mfa_pending": False,
        "next_aal": state.get("next_aal"),
        "mfa_enrollment_required": False,
    }


@method_decorator(ensure_csrf_cookie, name="dispatch")
class CsrfView(APIView):
    """GET: set csrftoken cookie for subsequent unsafe requests."""

    permission_classes = [AllowAny]
    authentication_classes = []

    def get(self, request):
        return Response({"detail": "CSRF cookie set"})


class LoginView(APIView):
    permission_classes = [AllowAny]
    throttle_classes = [LoginRateThrottle]

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

        return Response(
            _finish_primary_auth(request, user, data, auth_method="supabase_password"),
            status=status.HTTP_200_OK,
        )


class SignupView(APIView):
    permission_classes = [AllowAny]
    throttle_classes = [SignupRateThrottle]

    def post(self, request):
        email = (request.data.get("email") or "").strip()
        password = request.data.get("password") or ""
        confirm_redirect_to = _email_confirmation_signup_target()
        frontend_confirm_redirect_to = _email_confirmation_frontend_target()
        if not email or not password:
            return Response(
                {"detail": "email and password are required", "error_code": "validation_error"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            data = supabase_signup(
                email,
                password,
                email_redirect_to=confirm_redirect_to,
            )
        except SupabaseAuthError as exc:
            return Response(
                {"detail": exc.message, "error_code": exc.error_code},
                status=status.HTTP_400_BAD_REQUEST,
            )
        access = data.get("access_token")
        if not access:
            out = {
                "email_confirmation_required": True,
                "requires_verification": True,
            }
            if frontend_confirm_redirect_to:
                out["email_confirmation_redirect_to"] = frontend_confirm_redirect_to
            return Response(out, status=status.HTTP_200_OK)
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
        return Response(
            _finish_primary_auth(request, user, data, auth_method="supabase_signup"),
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
    authentication_classes = [AuthSessionAuthentication]
    permission_classes = [IsAuthenticated]

    def get(self, request):
        refresh_session_tokens_if_needed(request)
        state = get_session_auth_state(request)
        return Response(_me_payload(request, state))

    def patch(self, request):
        # Cap length to stay within the first_name column and avoid storing
        # unbounded user-supplied input.
        name = (request.data.get("name") or "").strip()[:120]
        request.user.first_name = name
        request.user.save(update_fields=["first_name"])

        explicit = UserRawExplicit.objects.filter(user=request.user).first()
        if explicit is not None:
            explicit.display_name = name
            explicit.save(update_fields=["display_name", "updated_at"])

        refresh_session_tokens_if_needed(request)
        state = get_session_auth_state(request)
        request.user.refresh_from_db()
        return Response(_me_payload(request, state))


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
        raw_redirect = (request.data.get("redirect_uri_after") or "").strip()
        frontend_redirect = _safe_frontend_redirect(raw_redirect)
        if raw_redirect and frontend_redirect is None:
            return Response(
                {
                    "detail": "redirect_uri_after is not an allowed redirect target.",
                    "error_code": "invalid_redirect",
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
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
        return Response({"authorize_url": authorize_url})


@method_decorator(ensure_csrf_cookie, name="dispatch")
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
        # Re-validate the stored redirect target before using it (only
        # allowlisted values are stored, but never trust a stored value blindly).
        frontend = _safe_frontend_redirect(pkce_state.get("frontend_redirect")) or ""
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
            user, created = get_or_create_local_user_with_status(supabase_uid, canonical_email)
        except SupabaseAuthError as exc:
            return Response(
                {"detail": exc.message, "error_code": exc.error_code},
                status=status.HTTP_400_BAD_REQUEST,
            )
        provider = (pkce_state.get("provider") or "oauth").strip().lower()
        auth_payload = _finish_primary_auth(request, user, data, auth_method=f"oauth_{provider}")
        first_google_login = provider == "google" and created
        if first_google_login:
            auth_payload["oauth_first_login_confirmation_required"] = True
        if frontend:
            if auth_payload.get("error_code"):
                login_state = str(auth_payload.get("error_code"))
            elif first_google_login:
                login_state = "oauth_first_login_confirmation_required"
            else:
                login_state = "success"
            extra_params: dict[str, object] = {
                "next_step": auth_payload.get("next_step"),
                "post_mfa_step": auth_payload.get("post_mfa_step"),
            }
            if first_google_login:
                extra_params["first_login_confirmation_required"] = "1"
            return _auth_redirect(
                frontend,
                login_state,
                extra_params=extra_params,
            )
        return Response(auth_payload)


@method_decorator(ensure_csrf_cookie, name="dispatch")
class EmailConfirmCallbackView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []

    def get(self, request):
        frontend = _email_confirmation_frontend_target()
        err = request.query_params.get("error_description") or request.query_params.get("error")
        if err:
            if frontend:
                return _auth_redirect(frontend, "email_confirm_failed")
            return Response(
                {"detail": str(err), "error_code": "email_confirm_failed"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        token_hash = (request.query_params.get("token_hash") or "").strip()
        verify_type = (request.query_params.get("type") or "signup").strip().lower() or "signup"
        if not token_hash:
            if frontend:
                return _auth_redirect(frontend, "email_confirm_missing_token")
            return Response(
                {
                    "detail": "Missing token_hash for email confirmation.",
                    "error_code": "email_confirm_missing_token",
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            data = supabase_verify_email_token(token_hash, verify_type)
            access = str(data.get("access_token") or "").strip()
            if not access:
                raise SupabaseAuthError(
                    "Email confirmation did not return a session.",
                    error_code="email_confirm_no_session",
                )
            if not isinstance(data.get("user"), dict):
                data["user"] = supabase_get_user(access)
            supabase_uid, canonical_email = user_from_token_response(data)
            user = get_or_create_local_user(supabase_uid, canonical_email)
        except SupabaseAuthError as exc:
            if frontend:
                return _auth_redirect(frontend, exc.error_code or "email_confirm_failed")
            return Response(
                {"detail": exc.message, "error_code": exc.error_code},
                status=status.HTTP_400_BAD_REQUEST,
            )

        auth_payload = _finish_primary_auth(
            request,
            user,
            data,
            auth_method="supabase_email_confirm",
        )
        if frontend:
            login_state = str(auth_payload.get("error_code") or "success")
            return _auth_redirect(
                frontend,
                login_state,
                extra_params={
                    "next_step": auth_payload.get("next_step"),
                    "post_mfa_step": auth_payload.get("post_mfa_step"),
                },
            )
        return Response(auth_payload)


class MfaChallengeView(APIView):
    authentication_classes = [AuthSessionAuthentication]
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
    authentication_classes = [AuthSessionAuthentication]
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
    authentication_classes = [AuthSessionAuthentication]
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
    authentication_classes = [AuthSessionAuthentication]
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
            verify_out = supabase_mfa_verify(access, factor_id, challenge_id, code)
            access_after = str(verify_out.get("access_token") or access).strip()
            user_obj = supabase_get_user(access_after)
        except SupabaseAuthError as exc:
            audit_mfa_verify(request, request.user, "failure", exc.error_code)
            return Response(
                {"detail": exc.message, "error_code": exc.error_code},
                status=status.HTTP_400_BAD_REQUEST,
            )
        audit_mfa_verify(request, request.user, "success")
        return Response(_complete_mfa_verification(request, user_obj=user_obj, verify_out=verify_out))


class MfaVerifyView(APIView):
    authentication_classes = [AuthSessionAuthentication]
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
            verify_out = supabase_mfa_verify(access, factor_id, challenge_id, code)
            access_after = str(verify_out.get("access_token") or access).strip()
            user_obj = supabase_get_user(access_after)
        except SupabaseAuthError as exc:
            audit_mfa_verify(request, request.user, "failure", exc.error_code)
            return Response(
                {"detail": exc.message, "error_code": exc.error_code},
                status=status.HTTP_400_BAD_REQUEST,
            )
        audit_mfa_verify(request, request.user, "success")
        return Response(_complete_mfa_verification(request, user_obj=user_obj, verify_out=verify_out))


class MfaFactorView(APIView):
    authentication_classes = [AuthSessionAuthentication]
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
    authentication_classes = [AuthSessionAuthentication]
    permission_classes = [IsAuthenticated]

    def get(self, request):
        from .security_assurance import build_assurance_payload

        return Response(build_assurance_payload(request))
