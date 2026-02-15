# backend/users/supabase_auth.py

import os
import uuid
import requests
import jwt  # PyJWT
from jwt.algorithms import RSAAlgorithm

from django.contrib.auth import get_user_model
from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import AuthenticationFailed

User = get_user_model()

SUPABASE_URL = os.getenv("SUPABASE_URL")  # e.g. https://xxxx.supabase.co
SUPABASE_JWT_AUD = os.getenv("SUPABASE_JWT_AUD", "authenticated")

_JWKS_CACHE = None  # simple in-process cache


def _get_bearer_token(request):
    header = request.headers.get("Authorization") or ""
    if not header.startswith("Bearer "):
        return None
    return header.split(" ", 1)[1].strip()


def _get_jwks():
    global _JWKS_CACHE
    if _JWKS_CACHE is not None:
        return _JWKS_CACHE

    if not SUPABASE_URL:
        raise AuthenticationFailed("SUPABASE_URL missing in environment.")

    jwks_url = f"{SUPABASE_URL}/auth/v1/.well-known/jwks.json"
    try:
        resp = requests.get(jwks_url, timeout=5)
        resp.raise_for_status()
        _JWKS_CACHE = resp.json()
        return _JWKS_CACHE
    except Exception as e:
        raise AuthenticationFailed(f"Failed to fetch Supabase JWKS: {e}")


def _verify_and_decode(token: str) -> dict:
    jwks = _get_jwks()

    try:
        header = jwt.get_unverified_header(token)
        kid = header.get("kid")
        if not kid:
            raise AuthenticationFailed("JWT missing kid header.")
    except Exception as e:
        raise AuthenticationFailed(f"Invalid JWT header: {e}")

    key = None
    for jwk in jwks.get("keys", []):
        if jwk.get("kid") == kid:
            key = RSAAlgorithm.from_jwk(jwk)
            break

    if key is None:
        raise AuthenticationFailed("No matching JWKS key found for token.")

    try:
        payload = jwt.decode(
            token,
            key=key,
            algorithms=["RS256"],
            audience=SUPABASE_JWT_AUD,
            options={
                "verify_iss": False,  # can enforce later if you want
            },
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
        email = payload.get("email")

        if not sub:
            raise AuthenticationFailed("Supabase token missing sub.")
        if not email:
            raise AuthenticationFailed("Supabase token missing email.")

        try:
            supabase_uid = uuid.UUID(str(sub))
        except Exception:
            raise AuthenticationFailed("Supabase sub is not a valid UUID.")

        email_norm = str(email).strip().lower()

        # Primary lookup by supabase_uid
        user = User.objects.filter(supabase_uid=supabase_uid).first()

        if user is None:
            # Secondary: if a user exists by email, link it
            user = User.objects.filter(email__iexact=email_norm).first()
            if user is not None:
                user.supabase_uid = supabase_uid
                user.email = user.email or email_norm
                user.save(update_fields=["supabase_uid", "email"])
            else:
                # Create a new Django user row
                # username must be unique; use email if possible
                username = email_norm
                user = User.objects.create(
                    username=username,
                    email=email_norm,
                    supabase_uid=supabase_uid,
                )

        return (user, None)
