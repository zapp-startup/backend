import json
import logging
import os
import threading
import time
import uuid

import jwt  # PyJWT
from django.conf import settings

import requests
from jwt.algorithms import ECAlgorithm, RSAAlgorithm
from django.contrib.auth import get_user_model
from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import AuthenticationFailed

User = get_user_model()
logger = logging.getLogger(__name__)

# Module-level aliases kept for test patchability and backward compatibility.
SUPABASE_URL = getattr(settings, "SUPABASE_URL", None) or os.getenv("SUPABASE_URL")
SUPABASE_JWT_AUD = getattr(settings, "SUPABASE_JWT_AUD", None) or os.getenv("SUPABASE_JWT_AUD", "authenticated")
SUPABASE_ANON_KEY = getattr(settings, "SUPABASE_ANON_KEY", None) or os.getenv("SUPABASE_ANON_KEY")
SUPABASE_JWT_ISS = getattr(settings, "SUPABASE_JWT_ISS", None) or os.getenv("SUPABASE_JWT_ISS")
SUPABASE_JWKS_CACHE_TTL_SECONDS = getattr(
    settings,
    "SUPABASE_JWKS_CACHE_TTL_SECONDS",
    None,
) or int(os.getenv("SUPABASE_JWKS_CACHE_TTL_SECONDS", "300"))
SUPABASE_JWT_SECRET = getattr(settings, "SUPABASE_JWT_SECRET", None) or os.getenv("SUPABASE_JWT_SECRET")


# Read from Django settings (populated from env at startup) while honoring patched module constants in tests.
def _supabase_url():
    return SUPABASE_URL or getattr(settings, "SUPABASE_URL", None) or os.getenv("SUPABASE_URL")
def _supabase_jwt_aud():
    return SUPABASE_JWT_AUD or getattr(settings, "SUPABASE_JWT_AUD", None) or os.getenv("SUPABASE_JWT_AUD", "authenticated")
def _supabase_anon_key():
    return SUPABASE_ANON_KEY or getattr(settings, "SUPABASE_ANON_KEY", None) or os.getenv("SUPABASE_ANON_KEY")
def _supabase_jwt_iss():
    return SUPABASE_JWT_ISS or getattr(settings, "SUPABASE_JWT_ISS", None) or os.getenv("SUPABASE_JWT_ISS")
def _supabase_jwks_ttl():
    return (
        SUPABASE_JWKS_CACHE_TTL_SECONDS
        or getattr(settings, "SUPABASE_JWKS_CACHE_TTL_SECONDS", None)
        or int(os.getenv("SUPABASE_JWKS_CACHE_TTL_SECONDS", "300"))
    )
def _supabase_jwt_secret():
    return SUPABASE_JWT_SECRET or getattr(settings, "SUPABASE_JWT_SECRET", None) or os.getenv("SUPABASE_JWT_SECRET")


# How long (seconds) to suppress JWKS refreshes triggered by an unknown kid.
MISSING_KID_CACHE_TTL = 60
# Minimum seconds between outbound JWKS refresh attempts.
JWKS_REFRESH_BACKOFF = 30

_JWKS_LOCK = threading.Lock()
_JWKS_CACHE = None  # simple in-process cache
_JWKS_CACHE_EXPIRES_AT = 0.0
_LAST_JWKS_REFRESH_ATTEMPT = 0.0

# kid -> timestamp of first failed lookup; entries expire after MISSING_KID_CACHE_TTL.
_MISSING_KID_LOCK = threading.Lock()
_MISSING_KID_CACHE: dict = {}


def _get_bearer_token(request):
    header = request.headers.get("Authorization") or ""
    if not header.startswith("Bearer "):
        return None
    return header.split(" ", 1)[1].strip()


def _fetch_jwks(jwks_url: str):
    headers = {}
    anon_key = _supabase_anon_key()
    if anon_key:
        headers["apikey"] = anon_key

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
    """Read the canonical top-level email claim from a JWT payload or Supabase user profile."""
    if not data:
        return None

    email = data.get("email")
    if not email:
        return None
    return str(email).strip().lower()


def _is_email_verified(data: dict | None) -> bool:
    """Supabase marks verified emails with a non-null `email_confirmed_at` timestamp."""
    if not data:
        return False
    return bool(data.get("email_confirmed_at"))


def _extract_aal(payload: dict | None, user_data: dict | None) -> str | None:
    """Supabase JWT may include Authenticator Assurance Level (aal1 / aal2)."""
    if payload and payload.get("aal"):
        return str(payload["aal"]).lower()
    if user_data and user_data.get("aal"):
        return str(user_data["aal"]).lower()
    return None


def _extract_amr(payload: dict | None, user_data: dict | None) -> list:
    """Authentication methods references; list of strings when present."""
    for src in (payload, user_data):
        if not src:
            continue
        amr = src.get("amr")
        if isinstance(amr, list):
            return [str(x) for x in amr]
    return []


def _count_mfa_factors(user_data: dict | None) -> int:
    """Number of enrolled MFA factors from /auth/v1/user when available."""
    if not user_data:
        return -1
    factors = user_data.get("factors")
    if factors is None:
        return -1
    if isinstance(factors, list):
        return len(factors)
    return -1


def _extract_subject(payload: dict | None, user_data: dict | None) -> str | None:
    if payload and payload.get("sub"):
        return str(payload["sub"])
    if user_data and user_data.get("id"):
        return str(user_data["id"])
    return None


def _fetch_supabase_user(token: str) -> dict:
    supabase_url = _supabase_url()
    if not supabase_url:
        logger.error("SUPABASE_URL is missing while validating a Supabase user.")
        raise AuthenticationFailed("Authentication is not configured.")

    headers = {"Authorization": f"Bearer {token}"}
    anon_key = _supabase_anon_key()
    if anon_key:
        headers["apikey"] = anon_key

    try:
        response = requests.get(
            f"{supabase_url}/auth/v1/user",
            timeout=5,
            headers=headers,
        )
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
    """
    Build a unique username from an email-like base.
    """
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

        # Return the cached keyset if it is still fresh and no forced refresh was requested.
        if not force_refresh and _JWKS_CACHE is not None and now < _JWKS_CACHE_EXPIRES_AT:
            return _JWKS_CACHE

        # Backoff guard: only suppress refreshes when a prior JWKS cache exists.
        if _JWKS_CACHE is not None and now - _LAST_JWKS_REFRESH_ATTEMPT < JWKS_REFRESH_BACKOFF:
            if _JWKS_CACHE is not None:
                logger.debug(
                    "JWKS refresh skipped (backoff active, %.0fs remaining); using cached keys.",
                    JWKS_REFRESH_BACKOFF - (now - _LAST_JWKS_REFRESH_ATTEMPT),
                )
                return _JWKS_CACHE
        elif _JWKS_CACHE is None and _LAST_JWKS_REFRESH_ATTEMPT:
            logger.info("JWKS refresh retrying immediately because no cache is available.")

        supabase_url = _supabase_url()
        if not supabase_url:
            logger.error("SUPABASE_URL is missing while fetching Supabase JWKS.")
            raise AuthenticationFailed("Authentication is not configured.")

        jwks_urls = [
            f"{supabase_url}/auth/v1/.well-known/jwks.json",
            f"{supabase_url}/auth/v1/keys",
        ]

        _LAST_JWKS_REFRESH_ATTEMPT = now

        for jwks_url in jwks_urls:
            try:
                new_jwks = _fetch_jwks(jwks_url)
                _JWKS_CACHE = new_jwks
                _JWKS_CACHE_EXPIRES_AT = time.time() + _supabase_jwks_ttl()
                logger.info("JWKS refresh succeeded from %s.", jwks_url)
                return _JWKS_CACHE
            except AuthenticationFailed:
                continue

        if _JWKS_CACHE is not None:
            logger.warning("JWKS refresh failed; continuing with stale cached keys.")
            return _JWKS_CACHE

        raise AuthenticationFailed("Unable to validate Supabase token.")


def _verify_with_jwt_secret(token: str) -> dict | None:
    """Fallback for Supabase projects using HS256 with JWT secret (older projects)."""
    secret = _supabase_jwt_secret()
    issuer = _supabase_jwt_iss()
    if not secret or not issuer:
        return None
    try:
        return jwt.decode(
            token,
            secret,
            algorithms=["HS256"],
            audience=_supabase_jwt_aud(),
            issuer=issuer,
            options={"verify_signature": True, "verify_exp": True, "verify_aud": True, "verify_iss": True},
        )
    except Exception:
        return None


def _require_supabase_issuer() -> str:
    issuer = (_supabase_jwt_iss() or "").strip()
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
            audience=_supabase_jwt_aud(),
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
            return None  # DRF treats as unauthenticated

        payload = None
        user_data = None

        try:
            payload = _verify_and_decode(token)
        except AuthenticationFailed as e:
            logger.warning(
                "Supabase JWT local validation failed; falling back to JWT secret and /auth/v1/user: %s",
                e,
            )
            payload = _verify_with_jwt_secret(token)

        # Some valid Supabase access tokens omit claims this backend expects, so
        # fall back to the canonical user endpoint to finish validation/profile resolution.
        if payload is None or not _extract_email(payload) or not _is_email_verified(payload):
            user_data = _fetch_supabase_user(token)

        sub = _extract_subject(payload, user_data)
        email = _extract_email(payload) or _extract_email(user_data)

        if not sub:
            raise AuthenticationFailed("Supabase token missing sub/id.")
        if not email:
            raise AuthenticationFailed("Supabase token missing a usable top-level email claim.")

        if not (_is_email_verified(payload) or _is_email_verified(user_data)):
            raise AuthenticationFailed(
                "Supabase token missing a verified top-level email claim."
            )

        try:
            supabase_uid = uuid.UUID(str(sub))
        except Exception:
            raise AuthenticationFailed("Supabase sub is not a valid UUID.")


        # Primary lookup by supabase_uid
        user = User.objects.filter(supabase_uid=supabase_uid).first()

        if user is None:
            if User.objects.filter(email__iexact=email).exists():
                raise AuthenticationFailed(
                    "A local account with this email already exists and must be linked manually."
                )

            # Create a new Django user row with an unusable local password.
            user = User.objects.create_user(
                username=build_unique_username(email),
                email=email,
                supabase_uid=supabase_uid,
                password=None,
            )
        elif not user.email:
            user.email = email
            user.save(update_fields=["email"])

        auth_context = {
            "supabase_uid": str(sub),
            "email": email,
            "aal": _extract_aal(payload, user_data),
            "amr": _extract_amr(payload, user_data),
            "mfa_factors_count": _count_mfa_factors(user_data),
        }
        return (user, auth_context)
