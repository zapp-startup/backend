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
from django.contrib.auth import (
    BACKEND_SESSION_KEY,
    HASH_SESSION_KEY,
    SESSION_KEY,
    get_user_model,
    login,
)
from django.contrib.auth.models import AnonymousUser
from compliance.monitoring import capture_backend_audit_event

from .supabase_auth import build_unique_username
from zapp.security.data_encryption import decrypt_app_data, encrypt_app_data

logger = logging.getLogger(__name__)

User = get_user_model()


def _session_token_fingerprint(token: str | None) -> str:
    """Short hash for logs only; never log raw tokens."""
    if not token:
        return "none"
    return hashlib.sha256(token.encode("utf-8")).hexdigest()[:16]

AUTH_SESSION_KEY = "auth"
OAUTH_PKCE_SESSION_KEY = "oauth_pkce"

SUPABASE_TOKEN_PATH = "/auth/v1/token"
SUPABASE_SIGNUP_PATH = "/auth/v1/signup"
SUPABASE_LOGOUT_PATH = "/auth/v1/logout"
SUPABASE_AUTHORIZE_PATH = "/auth/v1/authorize"
SUPABASE_USER_PATH = "/auth/v1/user"
SUPABASE_VERIFY_PATH = "/auth/v1/verify"


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


def auth_requires_aal2() -> bool:
    return bool(getattr(settings, "AUTH_REQUIRE_AAL2", False))


def _normalize_factor_count(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return -1


def _normalize_aal(value: Any) -> str | None:
    if not value:
        return None
    return str(value).strip().lower() or None


def session_requires_pending_mfa(aal: Any, factor_count: Any) -> tuple[bool, bool]:
    if not auth_requires_aal2():
        return False, False
    if _normalize_aal(aal) == "aal2":
        return False, False
    return True, _normalize_factor_count(factor_count) == 0


def _clear_django_login_state(request) -> None:
    removed = False
    for key in (SESSION_KEY, BACKEND_SESSION_KEY, HASH_SESSION_KEY):
        if key in request.session:
            del request.session[key]
            removed = True
    if removed:
        request.user = AnonymousUser()
    request.session.modified = True


def _apply_mfa_policy_flags(request, state: dict[str, Any]) -> dict[str, Any]:
    pending, enrollment_required = session_requires_pending_mfa(
        state.get("aal"),
        state.get("mfa_factor_count"),
    )
    state["mfa_pending"] = pending
    state["next_aal"] = "aal2" if pending else None
    state["mfa_enrollment_required"] = enrollment_required if pending else False
    if pending:
        _clear_django_login_state(request)
    return state


def _should_preserve_verified_session(
    state: dict[str, Any],
    *,
    incoming_aal: str | None,
    incoming_factor_count: int,
) -> bool:
    """
    Keep the current browser session at aal2 after a successful MFA verify.

    Supabase's subsequent /user or refresh responses may report aal1 even though
    the current first-party session has already stepped up. For this browser
    session, treat the local step-up as authoritative unless the user now has no
    usable MFA factors left.
    """
    if _normalize_aal(state.get("aal")) != "aal2":
        return False
    if bool(state.get("mfa_pending")):
        return False
    if state.get("last_step_up_at") in (None, ""):
        return False
    if incoming_aal == "aal2":
        return False
    if incoming_factor_count == 0:
        return False
    return True


def _sync_session_assurance_state(
    request,
    state: dict[str, Any],
    *,
    incoming_aal: str | None,
    incoming_factor_count: int,
) -> None:
    preserve_verified = _should_preserve_verified_session(
        state,
        incoming_aal=incoming_aal,
        incoming_factor_count=incoming_factor_count,
    )
    if incoming_factor_count >= 0:
        state["mfa_factor_count"] = incoming_factor_count

    if preserve_verified:
        state["aal"] = "aal2"
        state["mfa_pending"] = False
        state["next_aal"] = None
        state["mfa_enrollment_required"] = False
        if state.get("last_step_up_at") in (None, ""):
            state["last_step_up_at"] = int(time.time())
        return

    if incoming_aal:
        state["aal"] = incoming_aal
    if _normalize_aal(state.get("aal")) != "aal2":
        state["last_step_up_at"] = None
    _apply_mfa_policy_flags(request, state)


def _build_auth_session_block(
    user,
    session_data: dict[str, Any],
    access_token: str,
    refresh_token: str | None,
    expires_in: int | None,
    *,
    pending_mfa: bool | None = None,
    mfa_enrollment_required: bool | None = None,
) -> dict[str, Any]:
    now_ts = int(time.time())
    aal = _normalize_aal(session_data.get("aal"))
    factor_count = _normalize_factor_count(session_data.get("mfa_factor_count", -1))
    last_step_up_at = now_ts if aal == "aal2" else None

    if pending_mfa is None or mfa_enrollment_required is None:
        derived_pending, derived_enrollment = session_requires_pending_mfa(aal, factor_count)
        if pending_mfa is None:
            pending_mfa = derived_pending
        if mfa_enrollment_required is None:
            mfa_enrollment_required = derived_enrollment

    return {
        "user_id": user.pk,
        "supabase_uid": str(user.supabase_uid) if user.supabase_uid else None,
        "email": user.email,
        "aal": aal,
        "auth_method": session_data.get("auth_method") or "password",
        "mfa_factor_count": factor_count,
        "mfa_pending": bool(pending_mfa),
        "next_aal": "aal2" if pending_mfa else None,
        "mfa_enrollment_required": bool(mfa_enrollment_required) if pending_mfa else False,
        "last_step_up_at": last_step_up_at,
        "logged_in_at": now_ts,
        "_supabase_access_token": encrypt_app_data(access_token),
        "_supabase_refresh_token": encrypt_app_data(refresh_token or ""),
        "_supabase_token_expires_at": _token_expires_at(access_token, expires_in),
    }


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


def supabase_signup(
    email: str,
    password: str,
    *,
    email_redirect_to: str | None = None,
) -> dict[str, Any]:
    url = _supabase_base_url() + SUPABASE_SIGNUP_PATH
    body: dict[str, Any] = {"email": email.strip(), "password": password}
    redirect_target = (email_redirect_to or "").strip()
    if redirect_target:
        body["options"] = {"email_redirect_to": redirect_target}
    resp = requests.post(
        url,
        headers=_anon_headers(),
        json=body,
        timeout=10,
    )
    if resp.status_code not in (200, 201):
        msg, code = _parse_error_payload(resp)
        raise SupabaseAuthError(msg, status_code=400, error_code=code or "signup_failed")
    return resp.json()


def supabase_verify_email_token(token_hash: str, verify_type: str = "signup") -> dict[str, Any]:
    """
    Exchange a Supabase email confirmation token hash for a session.
    """
    url = _supabase_base_url() + SUPABASE_VERIFY_PATH
    token = (token_hash or "").strip()
    token_type = (verify_type or "signup").strip().lower() or "signup"
    resp = requests.post(
        url,
        headers=_anon_headers(),
        json={"token_hash": token, "type": token_type},
        timeout=10,
    )
    if resp.status_code not in (200, 201):
        msg, code = _parse_error_payload(resp)
        raise SupabaseAuthError(msg, status_code=400, error_code=code or "email_confirm_failed")
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
        usable = 0
        for factor in factors:
            if not isinstance(factor, dict):
                continue
            factor_id = str(factor.get("id") or "").strip()
            if not factor_id:
                continue
            status = str(factor.get("status") or "").strip().lower()
            if status in ("", "verified"):
                usable += 1
        return usable
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


def get_or_create_local_user_with_status(supabase_uid: uuid.UUID, email: str) -> tuple[Any, bool]:
    user = User.objects.filter(supabase_uid=supabase_uid).first()
    if user is not None:
        if not user.email and email:
            user.email = email
            user.save(update_fields=["email"])
        return user, False

    if User.objects.filter(email__iexact=email).exists():
        raise SupabaseAuthError(
            "A local account with this email already exists and must be linked manually.",
            status_code=409,
            error_code="email_link_conflict",
        )

    created = User.objects.create_user(
        username=build_unique_username(email),
        email=email.strip().lower(),
        supabase_uid=supabase_uid,
        password=None,
    )
    return created, True


def get_or_create_local_user(supabase_uid: uuid.UUID, email: str) -> Any:
    user, _ = get_or_create_local_user_with_status(supabase_uid, email)
    return user


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
    request.session[AUTH_SESSION_KEY] = _build_auth_session_block(
        user,
        session_data,
        access_token,
        refresh_token,
        expires_in,
        pending_mfa=False,
        mfa_enrollment_required=False,
    )
    request.session.modified = True
    login(request, user, backend="django.contrib.auth.backends.ModelBackend")


def issue_pending_mfa_session(
    request,
    user,
    session_data: dict[str, Any],
    access_token: str,
    refresh_token: str | None,
    expires_in: int | None,
    *,
    mfa_enrollment_required: bool,
) -> None:
    request.session.flush()
    request.session[AUTH_SESSION_KEY] = _build_auth_session_block(
        user,
        session_data,
        access_token,
        refresh_token,
        expires_in,
        pending_mfa=True,
        mfa_enrollment_required=mfa_enrollment_required,
    )
    request.session.modified = True


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


def get_supabase_refresh_token(request) -> str | None:
    state = get_session_auth_state(request)
    if not state:
        return None
    tok = state.get("_supabase_refresh_token")
    if not isinstance(tok, str) or not tok:
        return None
    plaintext, _ = decrypt_app_data(tok)
    return plaintext if plaintext else None


def update_supabase_tokens(
    request,
    *,
    access_token: str | None,
    refresh_token: str | None,
    expires_in: int | None,
) -> None:
    state = get_session_auth_state(request)
    if not state:
        return
    if access_token:
        state["_supabase_access_token"] = encrypt_app_data(access_token)
        state["_supabase_token_expires_at"] = _token_expires_at(access_token, expires_in)
    if refresh_token is not None:
        state["_supabase_refresh_token"] = encrypt_app_data(refresh_token)
    request.session[AUTH_SESSION_KEY] = state
    request.session.modified = True


def record_step_up(request) -> None:
    state = get_session_auth_state(request)
    if not state:
        return
    now_ts = int(time.time())
    state["aal"] = "aal2"
    state["last_step_up_at"] = now_ts
    state["mfa_pending"] = False
    state["next_aal"] = None
    state["mfa_enrollment_required"] = False
    request.session[AUTH_SESSION_KEY] = state
    request.session.modified = True
    logger.debug(
        "[session_step_up] user_id=%s last_step_up_at=%s aal=%s",
        state.get("user_id"),
        now_ts,
        state.get("aal"),
    )


def sync_session_assurance_from_user(request, user_obj: dict[str, Any] | None) -> None:
    state = get_session_auth_state(request)
    if not state or not isinstance(user_obj, dict):
        return

    incoming_aal = _extract_aal(user_obj)
    incoming_factor_count = _count_mfa_factors(user_obj)
    preserve_verified = _should_preserve_verified_session(
        dict(state),
        incoming_aal=incoming_aal,
        incoming_factor_count=incoming_factor_count,
    )
    _sync_session_assurance_state(
        request,
        state,
        incoming_aal=incoming_aal,
        incoming_factor_count=incoming_factor_count,
    )
    request.session[AUTH_SESSION_KEY] = state
    request.session.modified = True
    logger.debug(
        "[session_assurance] user_id=%s preserve_verified=%s incoming_aal=%s "
        "incoming_factor_count=%s session_aal=%s mfa_pending=%s",
        state.get("user_id"),
        preserve_verified,
        incoming_aal,
        incoming_factor_count,
        state.get("aal"),
        state.get("mfa_pending"),
    )


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
    next_level = "aal2" if _count_mfa_factors(data) > 0 and current_level != "aal2" else None
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
    aal_before = _normalize_aal(state.get("aal"))
    old_access = get_supabase_access_token(request)
    old_fp = _session_token_fingerprint(old_access)
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
    new_fp = _session_token_fingerprint(access)
    user_obj = data.get("user")
    preserve_verified = False
    if isinstance(user_obj, dict):
        preserve_verified = _should_preserve_verified_session(
            dict(state),
            incoming_aal=_extract_aal(user_obj),
            incoming_factor_count=_count_mfa_factors(user_obj),
        )
        _sync_session_assurance_state(
            request,
            state,
            incoming_aal=_extract_aal(user_obj),
            incoming_factor_count=_count_mfa_factors(user_obj),
        )
    else:
        _apply_mfa_policy_flags(request, state)
    request.session[AUTH_SESSION_KEY] = state
    request.session.modified = True
    aal_after = _normalize_aal(state.get("aal"))
    logger.info(
        "[session_refresh] user_id=%s aal_before=%s aal_after=%s preserve_verified=%s "
        "access_fp_old=%s access_fp_new=%s",
        state.get("user_id"),
        aal_before,
        aal_after,
        preserve_verified if isinstance(user_obj, dict) else False,
        old_fp,
        new_fp,
    )
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
