# backend/users/supabase_auth.py

import json
import os
import uuid

import jwt  # PyJWT
import requests
from jwt.algorithms import ECAlgorithm, RSAAlgorithm
from django.contrib.auth import get_user_model
from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import AuthenticationFailed

User = get_user_model()

SUPABASE_URL = os.getenv("SUPABASE_URL")  # e.g. https://xxxx.supabase.co
SUPABASE_JWT_AUD = os.getenv("SUPABASE_JWT_AUD", "authenticated")
SUPABASE_ANON_KEY = os.getenv("SUPABASE_ANON_KEY")
SUPABASE_JWT_ISS = os.getenv("SUPABASE_JWT_ISS")  # e.g. https://xxxx.supabase.co/auth/v1

_JWKS_CACHE = None  # simple in-process cache


def _get_bearer_token(request):
    header = request.headers.get("Authorization") or ""
    if not header.startswith("Bearer "):
        return None
    return header.split(" ", 1)[1].strip()


def _fetch_jwks(jwks_url: str):
    headers = {}
    if SUPABASE_ANON_KEY:
        headers["apikey"] = SUPABASE_ANON_KEY

    resp = requests.get(jwks_url, timeout=5, headers=headers or None)
    resp.raise_for_status()

    content_type = resp.headers.get("content-type", "")
    if "application/json" not in content_type:
        raise AuthenticationFailed(
            f"JWKS not JSON (content-type={content_type}) from {jwks_url}: {resp.text[:200]}"
        )

    return resp.json()


def _extract_email(payload: dict) -> str | None:
    """Read only the canonical top-level email claim from the signed JWT payload."""
    email = payload.get("email")
    if not email:
        return None
    return str(email).strip().lower()


def _is_email_verified(payload: dict) -> bool:
    """Supabase marks verified emails with a non-null `email_confirmed_at` timestamp."""
    return bool(payload.get("email_confirmed_at"))


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
    global _JWKS_CACHE
    if _JWKS_CACHE is not None:
        return _JWKS_CACHE

    if not SUPABASE_URL:
        raise AuthenticationFailed("SUPABASE_URL missing in environment.")

    jwks_urls = [
        f"{SUPABASE_URL}/auth/v1/.well-known/jwks.json",
        f"{SUPABASE_URL}/auth/v1/keys",
    ]

    errors = []
    for jwks_url in jwks_urls:
        try:
            _JWKS_CACHE = _fetch_jwks(jwks_url)
            return _JWKS_CACHE
        except Exception as e:
            errors.append(f"{jwks_url}: {e}")

    raise AuthenticationFailed(
        "Failed to fetch Supabase JWKS from known endpoints: " + " | ".join(errors)
    )


def _verify_and_decode(token: str) -> dict:
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
        raise AuthenticationFailed(f"Invalid JWT header: {e}")

    jwk = next((k for k in jwks.get("keys", []) if k.get("kid") == kid), None)
    if not jwk:
        raise AuthenticationFailed("No matching JWKS key found for token.")

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
        raise AuthenticationFailed(f"Failed to parse JWKS key (kty={kty}): {e}")

    # Extra safety: ensure token alg matches what the key type implies
    if alg not in allowed_algs:
        raise AuthenticationFailed(
            f"JWT alg={alg} does not match key type kty={kty} (expected one of {allowed_algs})."
        )

    options = {
        "verify_signature": True,
        "verify_exp": True,
        "verify_aud": True,
        "verify_iss": bool(SUPABASE_JWT_ISS),  # only enforce if set
    }

    try:
        payload = jwt.decode(
            token,
            key=key,
            algorithms=allowed_algs,
            audience=SUPABASE_JWT_AUD,
            issuer=SUPABASE_JWT_ISS if SUPABASE_JWT_ISS else None,
            options=options,
        )
        return payload
    except Exception as e:
        raise AuthenticationFailed(f"Invalid Supabase token: {e}")


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
        email_verified = _is_email_verified(payload)

        if not sub:
            raise AuthenticationFailed("Supabase token missing sub.")
        if not email or not email_verified:
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
            # Secondary: if a user exists by email, link it
            user = User.objects.filter(email__iexact=email).first()
            if user is not None:
                fields_to_update = []
                if user.supabase_uid != supabase_uid:
                    user.supabase_uid = supabase_uid
                    fields_to_update.append("supabase_uid")
                if not user.email:
                    user.email = email
                    fields_to_update.append("email")
                if fields_to_update:
                    user.save(update_fields=fields_to_update)
            else:
                # Create a new Django user row
                # username must be unique; use email if possible
                user = User.objects.create(
                    username=build_unique_username(email),
                    email=email,
                    supabase_uid=supabase_uid,
                )
        elif not user.email:
            user.email = email
            user.save(update_fields=["email"])

        return (user, None)
