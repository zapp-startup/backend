import json
import logging
import os
import threading
import time
import uuid

import jwt
import requests
from django.contrib.auth import get_user_model
from jwt.algorithms import ECAlgorithm, RSAAlgorithm
from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import AuthenticationFailed

User = get_user_model()
logger = logging.getLogger(__name__)

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_JWT_AUD = os.getenv("SUPABASE_JWT_AUD", "authenticated")
SUPABASE_ANON_KEY = os.getenv("SUPABASE_ANON_KEY")
SUPABASE_JWT_ISS = os.getenv("SUPABASE_JWT_ISS")
SUPABASE_JWKS_CACHE_TTL_SECONDS = int(os.getenv("SUPABASE_JWKS_CACHE_TTL_SECONDS", "300"))

MISSING_KID_CACHE_TTL = 60
JWKS_REFRESH_BACKOFF = 30

_JWKS_LOCK = threading.Lock()
_JWKS_CACHE = None
_JWKS_CACHE_EXPIRES_AT = 0.0
_LAST_JWKS_REFRESH_ATTEMPT = 0.0

_MISSING_KID_LOCK = threading.Lock()
_MISSING_KID_CACHE = {}


def _get_bearer_token(request):
    header = request.headers.get("Authorization") or ""
    if not header.startswith("Bearer "):
        return None
    return header.split(" ", 1)[1].strip()


def _fetch_jwks(jwks_url: str):
    headers = {}
    if SUPABASE_ANON_KEY:
        headers["apikey"] = SUPABASE_ANON_KEY

    try:
        response = requests.get(jwks_url, timeout=5, headers=headers or None)
        response.raise_for_status()
    except requests.RequestException as exc:
        logger.warning("Failed to fetch Supabase JWKS from %s: %s", jwks_url, exc)
        raise AuthenticationFailed("Unable to validate Supabase token.")

    content_type = response.headers.get("content-type", "")
    if "application/json" not in content_type:
        logger.warning(
            "Supabase JWKS endpoint %s returned unexpected content-type %s",
            jwks_url,
            content_type,
        )
        raise AuthenticationFailed("Unable to validate Supabase token.")

    try:
        return response.json()
    except ValueError as exc:
        logger.warning("Supabase JWKS endpoint %s returned invalid JSON: %s", jwks_url, exc)
        raise AuthenticationFailed("Unable to validate Supabase token.")


def _extract_email(data: dict | None) -> str | None:
    if not data:
        return None

    email = data.get("email")
    if not email:
        return None
    return str(email).strip().lower()


def _is_email_verified(data: dict | None) -> bool:
    if not data:
        return False
    return bool(data.get("email_confirmed_at"))


def _extract_subject(payload: dict | None, user_data: dict | None) -> str | None:
    if payload and payload.get("sub"):
        return str(payload["sub"])
    if user_data and user_data.get("id"):
        return str(user_data["id"])
    return None


def _fetch_supabase_user(token: str) -> dict:
    if not SUPABASE_URL:
        logger.error("SUPABASE_URL is missing while validating a Supabase user.")
        raise AuthenticationFailed("Authentication is not configured.")

    headers = {"Authorization": f"Bearer {token}"}
    if SUPABASE_ANON_KEY:
        headers["apikey"] = SUPABASE_ANON_KEY

    try:
        response = requests.get(f"{SUPABASE_URL}/auth/v1/user", timeout=5, headers=headers)
        response.raise_for_status()
    except requests.RequestException as exc:
        logger.warning("Failed to fetch Supabase user profile: %s", exc)
        raise AuthenticationFailed("Unable to validate Supabase token.")

    content_type = response.headers.get("content-type", "")
    if "application/json" not in content_type:
        logger.warning(
            "Supabase user endpoint returned unexpected content-type %s",
            content_type,
        )
        raise AuthenticationFailed("Unable to validate Supabase token.")

    try:
        user_data = response.json()
    except ValueError as exc:
        logger.warning("Supabase user endpoint returned invalid JSON: %s", exc)
        raise AuthenticationFailed("Unable to validate Supabase token.")

    if not isinstance(user_data, dict):
        logger.warning("Supabase user endpoint returned a non-object payload.")
        raise AuthenticationFailed("Unable to validate Supabase token.")

    return user_data


def build_unique_username(base: str) -> str:
    candidate = (base or "").strip().lower() or f"user-{uuid.uuid4().hex[:8]}"
    if not User.objects.filter(username=candidate).exists():
        return candidate

    stem = candidate.split("@", 1)[0] if "@" in candidate else candidate
    for index in range(1, 1000):
        next_candidate = f"{stem}-{index}"
        if not User.objects.filter(username=next_candidate).exists():
            return next_candidate

    return f"{stem}-{uuid.uuid4().hex[:8]}"


def _get_jwks():
    return _get_jwks_with_refresh(force_refresh=False)


def _get_jwks_with_refresh(force_refresh: bool):
    global _JWKS_CACHE, _JWKS_CACHE_EXPIRES_AT, _LAST_JWKS_REFRESH_ATTEMPT

    with _JWKS_LOCK:
        now = time.time()

        if not force_refresh and _JWKS_CACHE is not None and now < _JWKS_CACHE_EXPIRES_AT:
            return _JWKS_CACHE

        if _JWKS_CACHE is not None and now - _LAST_JWKS_REFRESH_ATTEMPT < JWKS_REFRESH_BACKOFF:
            return _JWKS_CACHE

        if not SUPABASE_URL:
            logger.error("SUPABASE_URL is missing while fetching Supabase JWKS.")
            raise AuthenticationFailed("Authentication is not configured.")

        jwks_urls = [
            f"{SUPABASE_URL}/auth/v1/.well-known/jwks.json",
            f"{SUPABASE_URL}/auth/v1/keys",
        ]

        _LAST_JWKS_REFRESH_ATTEMPT = now

        for jwks_url in jwks_urls:
            try:
                new_jwks = _fetch_jwks(jwks_url)
                _JWKS_CACHE = new_jwks
                _JWKS_CACHE_EXPIRES_AT = time.time() + SUPABASE_JWKS_CACHE_TTL_SECONDS
                return _JWKS_CACHE
            except AuthenticationFailed:
                continue

        if _JWKS_CACHE is not None:
            logger.warning("JWKS refresh failed; continuing with stale cached keys.")
            return _JWKS_CACHE

        raise AuthenticationFailed("Unable to validate Supabase token.")


def _require_supabase_issuer() -> str:
    issuer = (SUPABASE_JWT_ISS or "").strip()
    if not issuer:
        logger.error("SUPABASE_JWT_ISS is not configured.")
        raise AuthenticationFailed("Authentication is not configured.")
    return issuer


def _verify_and_decode(token: str) -> dict:
    issuer = _require_supabase_issuer()
    jwks = _get_jwks()

    try:
        header = jwt.get_unverified_header(token)
        kid = header.get("kid")
        alg = header.get("alg")
        if not kid:
            raise AuthenticationFailed("JWT missing kid header.")
        if not alg:
            raise AuthenticationFailed("JWT missing alg header.")
    except AuthenticationFailed:
        raise
    except Exception as exc:
        logger.warning("Failed to parse Supabase JWT header: %s", exc)
        raise AuthenticationFailed("Unable to validate Supabase token.")

    jwk = next((item for item in jwks.get("keys", []) if item.get("kid") == kid), None)
    if not jwk:
        now = time.time()
        with _MISSING_KID_LOCK:
            cached_at = _MISSING_KID_CACHE.get(kid)
            if cached_at is not None and now - cached_at < MISSING_KID_CACHE_TTL:
                raise AuthenticationFailed("Unable to validate Supabase token.")

        cache_is_stale = now >= _JWKS_CACHE_EXPIRES_AT
        if cache_is_stale:
            jwks = _get_jwks_with_refresh(force_refresh=True)
            jwk = next((item for item in jwks.get("keys", []) if item.get("kid") == kid), None)

        if not jwk:
            with _MISSING_KID_LOCK:
                _MISSING_KID_CACHE[kid] = now
                expired = [
                    key for key, ts in list(_MISSING_KID_CACHE.items()) if now - ts >= MISSING_KID_CACHE_TTL
                ]
                for key in expired:
                    del _MISSING_KID_CACHE[key]
            raise AuthenticationFailed("Unable to validate Supabase token.")

    kty = jwk.get("kty")
    try:
        if kty == "RSA":
            key = RSAAlgorithm.from_jwk(json.dumps(jwk))
            allowed_algs = ["RS256"]
        elif kty == "EC":
            key = ECAlgorithm.from_jwk(json.dumps(jwk))
            allowed_algs = ["ES256"]
        else:
            raise AuthenticationFailed(f"Unsupported JWKS key type kty={kty}.")
    except AuthenticationFailed:
        raise
    except Exception as exc:
        logger.warning("Failed to parse JWKS key for Supabase token: %s", exc)
        raise AuthenticationFailed("Unable to validate Supabase token.")

    if alg not in allowed_algs:
        raise AuthenticationFailed("Unable to validate Supabase token.")

    try:
        return jwt.decode(
            token,
            key=key,
            algorithms=allowed_algs,
            audience=SUPABASE_JWT_AUD,
            issuer=issuer,
            options={
                "verify_signature": True,
                "verify_exp": True,
                "verify_aud": True,
                "verify_iss": True,
            },
        )
    except Exception as exc:
        logger.warning("Supabase token validation failed: %s", exc)
        raise AuthenticationFailed("Unable to validate Supabase token.")


class SupabaseJWTAuthentication(BaseAuthentication):
    """
    DRF authentication backend for Supabase access tokens.

    This branch still uses Django's default auth user table, so the backend links
    Supabase sessions by verified email instead of persisting the Supabase UUID.
    """

    def authenticate(self, request):
        token = _get_bearer_token(request)
        if not token:
            return None

        payload = None
        user_data = None

        try:
            payload = _verify_and_decode(token)
        except AuthenticationFailed as exc:
            logger.warning(
                "Supabase JWT local validation failed; falling back to /auth/v1/user lookup: %s",
                exc,
            )

        if payload is None or not _extract_email(payload) or not _is_email_verified(payload):
            user_data = _fetch_supabase_user(token)

        subject = _extract_subject(payload, user_data)
        email = _extract_email(payload) or _extract_email(user_data)

        if not subject:
            raise AuthenticationFailed("Supabase token missing sub/id.")
        if not email:
            raise AuthenticationFailed("Supabase token missing a usable top-level email claim.")
        if not (_is_email_verified(payload) or _is_email_verified(user_data)):
            raise AuthenticationFailed("Supabase token missing a verified top-level email claim.")

        user = User.objects.filter(email__iexact=email).first()
        if user is None:
            user = User.objects.create_user(
                username=build_unique_username(email),
                email=email,
                password=None,
            )
        elif not user.email:
            user.email = email
            user.save(update_fields=["email"])

        auth_context = {
            "supabase_uid": str(subject),
            "email": email,
        }
        return (user, auth_context)
