# backend/users/supabase_auth.py

import json
import logging
import os
import threading
import time
import uuid

import jwt  # PyJWT
import requests
from jwt.algorithms import ECAlgorithm, RSAAlgorithm
from django.contrib.auth import get_user_model
from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import AuthenticationFailed

User = get_user_model()
logger = logging.getLogger(__name__)

SUPABASE_URL = os.getenv("SUPABASE_URL")  # e.g. https://xxxx.supabase.co
SUPABASE_JWT_AUD = os.getenv("SUPABASE_JWT_AUD", "authenticated")
SUPABASE_ANON_KEY = os.getenv("SUPABASE_ANON_KEY")
SUPABASE_JWT_ISS = os.getenv("SUPABASE_JWT_ISS")  # e.g. https://xxxx.supabase.co/auth/v1
SUPABASE_JWKS_CACHE_TTL_SECONDS = int(os.getenv("SUPABASE_JWKS_CACHE_TTL_SECONDS", "300"))

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
    if SUPABASE_ANON_KEY:
        headers["apikey"] = SUPABASE_ANON_KEY

    try:
        resp = requests.get(jwks_url, timeout=5, headers=headers or None)
        resp.raise_for_status()
    except requests.RequestException as e:
        logger.warning("Failed to fetch Supabase JWKS from %s: %s", jwks_url, e)
        raise AuthenticationFailed("Unable to validate Supabase token.")

    content_type = resp.headers.get("content-type", "")
    if "application/json" not in content_type:
        logger.warning(
            "Supabase JWKS endpoint %s returned unexpected content-type %s",
            jwks_url,
            content_type,
        )
        raise AuthenticationFailed("Unable to validate Supabase token.")

    try:
        return resp.json()
    except ValueError as e:
        logger.warning("Supabase JWKS endpoint %s returned invalid JSON: %s", jwks_url, e)
        raise AuthenticationFailed("Unable to validate Supabase token.")


def _extract_email(payload: dict) -> str | None:
    """Read only the canonical top-level email claim from the signed JWT payload."""
    email = payload.get("email")
    if not email:
        return None
    return str(email).strip().lower()


def _is_email_verified(payload: dict) -> bool:
    """Supabase marks verified emails with a non-null `email_confirmed_at` timestamp."""
    return bool(payload.get("email_confirmed_at"))


def _fetch_supabase_user(token: str) -> dict:
    if not SUPABASE_URL:
        logger.error("SUPABASE_URL is missing while validating a Supabase user.")
        raise AuthenticationFailed("Authentication is not configured.")

    headers = {"Authorization": f"Bearer {token}"}
    if SUPABASE_ANON_KEY:
        headers["apikey"] = SUPABASE_ANON_KEY

    try:
        resp = requests.get(
            f"{SUPABASE_URL}/auth/v1/user",
            timeout=5,
            headers=headers,
        )
        resp.raise_for_status()
    except requests.RequestException as e:
        logger.warning("Failed to fetch Supabase user profile: %s", e)
        raise AuthenticationFailed("Unable to validate Supabase token.")

    content_type = resp.headers.get("content-type", "")
    if "application/json" not in content_type:
        logger.warning(
            "Supabase user endpoint returned unexpected content-type %s",
            content_type,
        )
        raise AuthenticationFailed("Unable to validate Supabase token.")

    try:
        user_data = resp.json()
    except ValueError as e:
        logger.warning("Supabase user endpoint returned invalid JSON: %s", e)
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
    for i in range(1, 1000):
        next_candidate = f"{stem}-{i}"
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

        if not SUPABASE_URL:
            logger.error("SUPABASE_URL is missing while fetching Supabase JWKS.")
            raise AuthenticationFailed("Authentication is not configured.")

        jwks_urls = [
            f"{SUPABASE_URL}/auth/v1/.well-known/jwks.json",
            f"{SUPABASE_URL}/auth/v1/keys",
        ]

        logger.info("JWKS refresh: attempting outbound fetch.")
        _LAST_JWKS_REFRESH_ATTEMPT = now

        for jwks_url in jwks_urls:
            try:
                new_jwks = _fetch_jwks(jwks_url)
                _JWKS_CACHE = new_jwks
                _JWKS_CACHE_EXPIRES_AT = time.time() + SUPABASE_JWKS_CACHE_TTL_SECONDS
                logger.info("JWKS refresh succeeded from %s.", jwks_url)
                return _JWKS_CACHE
            except AuthenticationFailed:
                continue

        # Every URL failed.  Keep the last known-good keyset to avoid a full auth outage.
        if _JWKS_CACHE is not None:
            logger.warning(
                "JWKS refresh failed for all endpoints; continuing with stale cached keys."
            )
            return _JWKS_CACHE

        logger.warning("JWKS refresh failed for all endpoints and no cached keys are available.")

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
    except Exception as e:
        logger.warning("Failed to parse Supabase JWT header: %s", e)
        raise AuthenticationFailed("Unable to validate Supabase token.")

    jwk = next((k for k in jwks.get("keys", []) if k.get("kid") == kid), None)
    if not jwk:
        now = time.time()

        # Negative-kid cache: if we recently confirmed this kid is not in the JWKS,
        # reject immediately without any outbound request.
        with _MISSING_KID_LOCK:
            cached_at = _MISSING_KID_CACHE.get(kid)
            if cached_at is not None and now - cached_at < MISSING_KID_CACHE_TTL:
                logger.warning(
                    "Rejected token: kid not in JWKS (negative cache hit, age=%.0fs).", now - cached_at
                )
                raise AuthenticationFailed("Unable to validate Supabase token.")

        # Only attempt a refresh when the JWKS cache itself is stale.  If the cache is
        # still fresh, the kid simply does not exist in the keyset — refresh would return
        # the same keys, so skip it and go straight to rejection.
        cache_is_stale = now >= _JWKS_CACHE_EXPIRES_AT
        if cache_is_stale:
            jwks = _get_jwks_with_refresh(force_refresh=True)
            jwk = next((k for k in jwks.get("keys", []) if k.get("kid") == kid), None)

        if not jwk:
            # Record the kid as unknown so subsequent requests are rejected immediately.
            with _MISSING_KID_LOCK:
                _MISSING_KID_CACHE[kid] = now
                # Prune stale negative-cache entries to keep memory bounded.
                expired = [k for k, ts in list(_MISSING_KID_CACHE.items()) if now - ts >= MISSING_KID_CACHE_TTL]
                for k in expired:
                    del _MISSING_KID_CACHE[k]
            logger.warning("Rejected token: kid not found in JWKS (cache_was_stale=%s).", cache_is_stale)
            raise AuthenticationFailed("Unable to validate Supabase token.")

    kty = jwk.get("kty")

    # Build the correct public key object from the JWK
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
    except Exception as e:
        logger.warning("Failed to parse JWKS key for Supabase token: %s", e)
        raise AuthenticationFailed("Unable to validate Supabase token.")

    # Extra safety: ensure token alg matches what the key type implies
    if alg not in allowed_algs:
        raise AuthenticationFailed(
            f"JWT alg={alg} does not match key type kty={kty} (expected one of {allowed_algs})."
        )

    options = {
        "verify_signature": True,
        "verify_exp": True,
        "verify_aud": True,
        "verify_iss": True,
    }

    try:
        payload = jwt.decode(
            token,
            key=key,
            algorithms=allowed_algs,
            audience=SUPABASE_JWT_AUD,
            issuer=issuer,
            options=options,
        )
        return payload
    except Exception as e:
        logger.warning("Supabase token validation failed: %s", e)
        raise AuthenticationFailed("Unable to validate Supabase token.")


class SupabaseJWTAuthentication(BaseAuthentication):
    """
    DRF authentication backend for Supabase access tokens.
    Expects: Authorization: Bearer <supabase_access_token>

    Maps Supabase user (payload['sub']) to a Django User.supabase_uid.
    Creates the Django user on first-seen Supabase login.
    """

    def authenticate(self, request):
        token = _get_bearer_token(request)
        if not token:
            return None  # DRF treats as unauthenticated

        payload = _verify_and_decode(token)

        sub = payload.get("sub")
        email = _extract_email(payload)

        if not sub:
            raise AuthenticationFailed("Supabase token missing sub.")
        if not email:
            raise AuthenticationFailed("Supabase token missing top-level email claim.")

        if not _is_email_verified(payload):
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

        return (user, None)
