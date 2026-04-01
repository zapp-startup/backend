"""
Django session BFF: server-side Supabase Auth calls and session state.

Supabase access/refresh tokens are stored only in the server session; they are
never returned to browser clients.
"""
from __future__ import annotations

import base64
import hashlib
import logging
import secrets
import time
import uuid
from typing import Any
from urllib.parse import urlencode

import jwt
import requests
from django.conf import settings
from django.contrib.auth import get_user_model, login
from compliance.monitoring import capture_backend_audit_event

from .supabase_auth import build_unique_username
from zapp.security.data_encryption import decrypt_app_data, encrypt_app_data

logger = logging.getLogger(__name__)

User = get_user_model()

AUTH_SESSION_KEY = "auth"
OAUTH_PKCE_SESSION_KEY = "oauth_pkce"

SUPABASE_TOKEN_PATH = "/auth/v1/token"
SUPABASE_SIGNUP_PATH = "/auth/v1/signup"
SUPABASE_LOGOUT_PATH = "/auth/v1/logout"
SUPABASE_AUTHORIZE_PATH = "/auth/v1/authorize"
SUPABASE_USER_PATH = "/auth/v1/user"


class SupabaseAuthError(Exception):
    def __init__(
        self,
        message: str,
        *,
        status_code: int = 400,
        error_code: str = "supabase_auth_error",
    ):
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.error_code = error_code


def _supabase_base_url() -> str:
    url = (getattr(settings, "SUPABASE_URL", None) or "").strip().rstrip("/")
    if not url:
        raise SupabaseAuthError(
            "Authentication is not configured.",
            status_code=503,
            error_code="auth_not_configured",
        )
    return url


def _anon_headers() -> dict[str, str]:
    key = (getattr(settings, "SUPABASE_ANON_KEY", None) or "").strip()
    if not key:
        raise SupabaseAuthError(
            "Authentication is not configured.",
            status_code=503,
            error_code="auth_not_configured",
        )
    return {
        "apikey": key,
        "Content-Type": "application/json",
    }


def _bearer_headers(access_token: str) -> dict[str, str]:
    h = _anon_headers()
    h["Authorization"] = f"Bearer {access_token}"
    return h


def _parse_error_payload(resp: requests.Response) -> tuple[str, str]:
    try:
        data = resp.json()
    except ValueError:
        return "Request failed", "request_failed"
    if isinstance(data, dict):
        msg = (
            data.get("error_description")
            or data.get("msg")
            or data.get("message")
            or data.get("error")
            or "Request failed"
        )
        code = str(data.get("error") or data.get("error_code") or "request_failed")
        return str(msg), code
    return "Request failed", "request_failed"


def supabase_login(email: str, password: str) -> dict[str, Any]:
    url = _supabase_base_url() + SUPABASE_TOKEN_PATH + "?grant_type=password"
    resp = requests.post(
        url,
        headers=_anon_headers(),
        json={"email": email.strip(), "password": password},
        timeout=10,
    )
    if resp.status_code != 200:
        msg, code = _parse_error_payload(resp)
        raise SupabaseAuthError(msg, status_code=400, error_code=code or "invalid_credentials")
    return resp.json()


def supabase_signup(email: str, password: str) -> dict[str, Any]:
    url = _supabase_base_url() + SUPABASE_SIGNUP_PATH
    resp = requests.post(
        url,
        headers=_anon_headers(),
        json={"email": email.strip(), "password": password},
        timeout=10,
    )
    if resp.status_code not in (200, 201):
        msg, code = _parse_error_payload(resp)
        raise SupabaseAuthError(msg, status_code=400, error_code=code or "signup_failed")
    return resp.json()


def supabase_logout(access_token: str) -> None:
    if not access_token:
        return
    url = _supabase_base_url() + SUPABASE_LOGOUT_PATH
    try:
        requests.post(
            url,
            headers=_bearer_headers(access_token),
            json={"scope": "global"},
            timeout=10,
        )
    except requests.RequestException as exc:
        logger.warning("Supabase logout request failed: %s", exc)


def supabase_refresh(refresh_token: str) -> dict[str, Any]:
    url = _supabase_base_url() + SUPABASE_TOKEN_PATH + "?grant_type=refresh_token"
    resp = requests.post(
        url,
        headers=_anon_headers(),
        json={"refresh_token": refresh_token},
        timeout=10,
    )
    if resp.status_code != 200:
        msg, code = _parse_error_payload(resp)
        raise SupabaseAuthError(msg, status_code=401, error_code=code or "refresh_failed")
    return resp.json()


def supabase_exchange_pkce(auth_code: str, code_verifier: str) -> dict[str, Any]:
    url = _supabase_base_url() + SUPABASE_TOKEN_PATH + "?grant_type=pkce"
    resp = requests.post(
        url,
        headers=_anon_headers(),
        json={"auth_code": auth_code, "code_verifier": code_verifier},
        timeout=10,
    )
    if resp.status_code != 200:
        msg, code = _parse_error_payload(resp)
        raise SupabaseAuthError(msg, status_code=400, error_code=code or "oauth_exchange_failed")
    return resp.json()


def supabase_mfa_challenge(access_token: str, factor_id: str) -> dict[str, Any]:
    url = f"{_supabase_base_url()}/auth/v1/factors/{factor_id}/challenge"
    resp = requests.post(url, headers=_bearer_headers(access_token), json={}, timeout=10)
    if resp.status_code not in (200, 201):
        msg, code = _parse_error_payload(resp)
        raise SupabaseAuthError(msg, status_code=400, error_code=code or "mfa_challenge_failed")
    return resp.json()


def supabase_mfa_verify(
    access_token: str, factor_id: str, challenge_id: str, code: str
) -> dict[str, Any]:
    url = f"{_supabase_base_url()}/auth/v1/factors/{factor_id}/verify"
    resp = requests.post(
        url,
        headers=_bearer_headers(access_token),
        json={"challenge_id": challenge_id, "code": code},
        timeout=10,
    )
    if resp.status_code not in (200, 201):
        msg, code = _parse_error_payload(resp)
        raise SupabaseAuthError(msg, status_code=400, error_code=code or "mfa_verify_failed")
    return resp.json()


def supabase_get_user(access_token: str) -> dict[str, Any]:
    url = f"{_supabase_base_url()}{SUPABASE_USER_PATH}"
    resp = requests.get(url, headers=_bearer_headers(access_token), timeout=10)
    if resp.status_code != 200:
        msg, code = _parse_error_payload(resp)
        raise SupabaseAuthError(msg, status_code=resp.status_code, error_code=code or "mfa_user_failed")
    data = resp.json()
    if not isinstance(data, dict):
        raise SupabaseAuthError("Invalid user response.", error_code="mfa_user_failed")
    return data


def supabase_mfa_enroll_totp(access_token: str, friendly_name: str) -> dict[str, Any]:
    url = f"{_supabase_base_url()}/auth/v1/factors"
    issuer = (getattr(settings, "PLAID_CLIENT_NAME", None) or "Zapp").strip() or "Zapp"
    resp = requests.post(
        url,
        headers=_bearer_headers(access_token),
        json={
            "factor_type": "totp",
            "friendly_name": friendly_name.strip() or "Authenticator app",
            "issuer": issuer,
        },
        timeout=10,
    )
    if resp.status_code not in (200, 201):
        msg, code = _parse_error_payload(resp)
        raise SupabaseAuthError(msg, status_code=400, error_code=code or "mfa_enroll_failed")
    return resp.json()


def supabase_mfa_unenroll(access_token: str, factor_id: str) -> None:
    url = f"{_supabase_base_url()}/auth/v1/factors/{factor_id}"
    resp = requests.delete(url, headers=_bearer_headers(access_token), timeout=10)
    if resp.status_code not in (200, 204):
        msg, code = _parse_error_payload(resp)
        raise SupabaseAuthError(msg, status_code=400, error_code=code or "mfa_unenroll_failed")


def _normalize_totp_qr_code(qr_code: Any) -> Any:
    if not isinstance(qr_code, str):
        return qr_code
    trimmed = qr_code.strip()
    if not trimmed:
        return qr_code
    if trimmed.lower().startswith("data:image/"):
        return trimmed
    if trimmed.startswith("<?xml") or trimmed.startswith("<svg"):
        encoded = base64.b64encode(trimmed.encode("utf-8")).decode("ascii")
        return f"data:image/svg+xml;base64,{encoded}"
    return qr_code


def normalize_mfa_enroll_payload(payload: Any) -> Any:
    """
    Supabase may return a raw SVG string for totp.qr_code. Normalize it to a
    data URI so browser clients can render it consistently.
    """
    if not isinstance(payload, dict):
        return payload
    totp = payload.get("totp")
    if not isinstance(totp, dict):
        return payload

    qr_code = _normalize_totp_qr_code(totp.get("qr_code"))
    if qr_code == totp.get("qr_code"):
        return payload

    normalized = dict(payload)
    normalized_totp = dict(totp)
    normalized_totp["qr_code"] = qr_code
    normalized["totp"] = normalized_totp
    return normalized


def _is_email_verified(user_obj: dict[str, Any] | None) -> bool:
    if not user_obj:
        return False
    return bool(user_obj.get("email_confirmed_at"))


def _count_mfa_factors(user_obj: dict[str, Any] | None) -> int:
    if not user_obj:
        return -1
    factors = user_obj.get("factors")
    if factors is None:
        return -1
    if isinstance(factors, list):
        return len(factors)
    return -1


def _extract_aal(user_obj: dict[str, Any] | None) -> str | None:
    if not user_obj:
        return None
    aal = user_obj.get("aal")
    return str(aal).lower() if aal else None


def _token_expires_at(access_token: str, expires_in: int | None) -> int:
    if expires_in is not None:
        return int(time.time()) + int(expires_in)
    try:
        decoded = jwt.decode(access_token, options={"verify_signature": False})
        exp = decoded.get("exp")
        if exp:
            return int(exp)
    except Exception:
        pass
    return int(time.time()) + 3600


def extract_session_data(sb_response: dict[str, Any]) -> dict[str, Any]:
    """Normalize user + assurance fields from a Supabase token or signup response."""
    user_obj = sb_response.get("user")
    if not isinstance(user_obj, dict):
        user_obj = {}
    aal = _extract_aal(user_obj)
    return {
        "aal": aal,
        "auth_method": "password",
        "mfa_factor_count": _count_mfa_factors(user_obj),
        "email_verified": _is_email_verified(user_obj),
    }


def get_or_create_local_user(supabase_uid: uuid.UUID, email: str) -> Any:
    user = User.objects.filter(supabase_uid=supabase_uid).first()
    if user is not None:
        if not user.email and email:
            user.email = email
            user.save(update_fields=["email"])
        return user

    if User.objects.filter(email__iexact=email).exists():
        raise SupabaseAuthError(
            "A local account with this email already exists and must be linked manually.",
            status_code=409,
            error_code="email_link_conflict",
        )

    return User.objects.create_user(
        username=build_unique_username(email),
        email=email.strip().lower(),
        supabase_uid=supabase_uid,
        password=None,
    )


def issue_django_session(
    request,
    user,
    session_data: dict[str, Any],
    access_token: str,
    refresh_token: str | None,
    expires_in: int | None,
) -> None:
    """Rotate session, log user in, store Supabase tokens server-side only."""
    request.session.flush()
    now_ts = int(time.time())
    aal = session_data.get("aal")
    last_step_up_at = None
    if aal == "aal2":
        last_step_up_at = now_ts

    request.session[AUTH_SESSION_KEY] = {
        "user_id": user.pk,
        "supabase_uid": str(user.supabase_uid) if user.supabase_uid else None,
        "email": user.email,
        "aal": aal,
        "auth_method": session_data.get("auth_method") or "password",
        "mfa_factor_count": session_data.get("mfa_factor_count", -1),
        "last_step_up_at": last_step_up_at,
        "logged_in_at": now_ts,
        "_supabase_access_token": encrypt_app_data(access_token),
        "_supabase_refresh_token": encrypt_app_data(refresh_token or ""),
        "_supabase_token_expires_at": _token_expires_at(access_token, expires_in),
    }
    request.session.modified = True
    login(request, user, backend="django.contrib.auth.backends.ModelBackend")


def clear_session(request) -> str | None:
    """Return access token for best-effort Supabase revoke; flush Django session."""
    auth_block = request.session.get(AUTH_SESSION_KEY) or {}
    access = None
    raw_access = auth_block.get("_supabase_access_token")
    if isinstance(raw_access, str) and raw_access:
        access, _ = decrypt_app_data(raw_access)
    try:
        request.session.flush()
    except Exception:
        request.session.clear()
    return access if isinstance(access, str) else None


def get_session_auth_state(request) -> dict[str, Any] | None:
    block = request.session.get(AUTH_SESSION_KEY)
    return block if isinstance(block, dict) else None


def get_supabase_access_token(request) -> str | None:
    state = get_session_auth_state(request)
    if not state:
        return None
    tok = state.get("_supabase_access_token")
    if not isinstance(tok, str) or not tok:
        return None
    plaintext, _ = decrypt_app_data(tok)
    return plaintext if plaintext else None


def record_step_up(request) -> None:
    state = get_session_auth_state(request)
    if not state:
        return
    now_ts = int(time.time())
    state["aal"] = "aal2"
    state["last_step_up_at"] = now_ts
    request.session[AUTH_SESSION_KEY] = state
    request.session.modified = True


def sync_session_assurance_from_user(request, user_obj: dict[str, Any] | None) -> None:
    state = get_session_auth_state(request)
    if not state or not isinstance(user_obj, dict):
        return

    aal = _extract_aal(user_obj)
    if aal:
        state["aal"] = aal
    count = _count_mfa_factors(user_obj)
    if count >= 0:
        state["mfa_factor_count"] = count
    if aal != "aal2":
        state["last_step_up_at"] = None
    request.session[AUTH_SESSION_KEY] = state
    request.session.modified = True


def build_mfa_snapshot_payload(user_obj: dict[str, Any] | None) -> dict[str, Any]:
    data = user_obj if isinstance(user_obj, dict) else {}
    factors_raw = data.get("factors")
    factors: list[dict[str, str]] = []
    if isinstance(factors_raw, list):
        for factor in factors_raw:
            if not isinstance(factor, dict):
                continue
            factor_id = str(factor.get("id") or "").strip()
            if not factor_id:
                continue
            factors.append(
                {
                    "id": factor_id,
                    "friendly_name": str(
                        factor.get("friendly_name") or factor.get("name") or "Authenticator"
                    ).strip()
                    or "Authenticator",
                    "factor_type": str(factor.get("factor_type") or "").strip() or "totp",
                    "status": str(factor.get("status") or "unverified").strip() or "unverified",
                }
            )

    current_level = _extract_aal(data)
    next_level = "aal2" if factors and current_level != "aal2" else None
    return {
        "current_level": current_level,
        "next_level": next_level,
        "factors": factors,
    }


def refresh_session_tokens_if_needed(request) -> bool:
    """
    If stored access token is near expiry, refresh using refresh_token.
    Returns True if session was updated.
    """
    state = get_session_auth_state(request)
    if not state:
        return False
    exp = state.get("_supabase_token_expires_at") or 0
    refresh_tok_encrypted = state.get("_supabase_refresh_token") or ""
    if not refresh_tok_encrypted or not isinstance(refresh_tok_encrypted, str):
        return False
    refresh_tok, _ = decrypt_app_data(refresh_tok_encrypted)
    if not refresh_tok:
        return False
    # Refresh if expiring within 120s
    if int(time.time()) < int(exp) - 120:
        return False
    try:
        data = supabase_refresh(refresh_tok)
    except SupabaseAuthError:
        return False
    access = data.get("access_token")
    if not access:
        return False
    new_refresh = data.get("refresh_token") or refresh_tok
    expires_in = data.get("expires_in")
    state["_supabase_access_token"] = encrypt_app_data(access)
    state["_supabase_refresh_token"] = encrypt_app_data(new_refresh)
    state["_supabase_token_expires_at"] = _token_expires_at(access, expires_in)
    user_obj = data.get("user")
    if isinstance(user_obj, dict):
        if user_obj.get("aal"):
            state["aal"] = str(user_obj["aal"]).lower()
        fc = _count_mfa_factors(user_obj)
        if fc >= 0:
            state["mfa_factor_count"] = fc
    request.session[AUTH_SESSION_KEY] = state
    request.session.modified = True
    return True


def audit_login(request, user, outcome: str, error_code: str | None = None, auth_method: str = "supabase_password") -> None:
    normalized_error_code = error_code or ("success" if outcome == "success" else "login_failed")

    capture_backend_audit_event(
        event_name="auth.login",
        outcome=outcome,
        actor=user if outcome == "success" else None,
        action="login",
        resource_type="session",
        request=request,
        status_code=200 if outcome == "success" else 400,
        error_code=normalized_error_code,
        error_message=None if outcome == "success" else (error_code or "failure"),
        metadata={"auth_method": auth_method},
    )


def audit_logout(request, user) -> None:
    capture_backend_audit_event(
        event_name="auth.logout",
        outcome="success",
        actor=user if getattr(user, "is_authenticated", False) else None,
        action="logout",
        resource_type="session",
        request=request,
        status_code=200,
        metadata={},
    )


def audit_mfa_verify(request, user, outcome: str, error_code: str | None = None) -> None:
    capture_backend_audit_event(
        event_name="auth.mfa_verify",
        outcome=outcome,
        actor=user,
        action="mfa_verify",
        resource_type="session",
        request=request,
        status_code=200 if outcome == "success" else 400,
        error_code=error_code or "",
        metadata={},
    )


def pkce_verifier() -> str:
    return base64.urlsafe_b64encode(secrets.token_bytes(32)).decode("ascii").rstrip("=")


def pkce_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def build_oauth_authorize_url(
    *,
    provider: str,
    redirect_to: str,
    code_challenge: str,
) -> str:
    q = urlencode(
        {
            "provider": provider,
            "redirect_to": redirect_to,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
        }
    )
    return f"{_supabase_base_url()}{SUPABASE_AUTHORIZE_PATH}?{q}"


def store_oauth_pkce_state(
    request,
    *,
    code_verifier: str,
    provider: str,
    frontend_redirect: str | None,
) -> None:
    request.session[OAUTH_PKCE_SESSION_KEY] = {
        "code_verifier": code_verifier,
        "provider": provider,
        "frontend_redirect": frontend_redirect or "",
    }
    request.session.modified = True
    request.session.save()

    print("PKCE SESSION SAVED")
    print("SESSION KEY:", request.session.session_key)
    print("SESSION DATA:", dict(request.session))


def pop_oauth_pkce_state(request) -> dict[str, Any] | None:
    data = request.session.pop(OAUTH_PKCE_SESSION_KEY, None)
    request.session.modified = True
    return data if isinstance(data, dict) else None


def user_from_token_response(sb_response: dict[str, Any]) -> tuple[uuid.UUID, str]:
    user_obj = sb_response.get("user")
    if not isinstance(user_obj, dict):
        raise SupabaseAuthError("Invalid auth response.", error_code="invalid_response")
    sub = user_obj.get("id")
    email = (user_obj.get("email") or "").strip().lower()
    if not sub or not email:
        raise SupabaseAuthError("Invalid user profile in auth response.", error_code="invalid_response")
    try:
        uid = uuid.UUID(str(sub))
    except Exception as exc:
        raise SupabaseAuthError("Invalid Supabase user id.", error_code="invalid_response") from exc
    if not _is_email_verified(user_obj):
        raise SupabaseAuthError(
            "Email is not verified.",
            status_code=403,
            error_code="email_not_confirmed",
        )
    return uid, email


def ensure_session_user_matches_request(request) -> None:
    """If session auth exists, ensure request.user aligns (after login)."""
    state = get_session_auth_state(request)
    if not state:
        return
    uid = state.get("user_id")
    if uid and request.user.is_authenticated and request.user.pk != uid:
        logger.warning("Session user_id mismatch with request.user; clearing session.")
        clear_session(request)
