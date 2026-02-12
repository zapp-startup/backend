import jwt
from django.conf import settings
from django.contrib.auth import get_user_model
from rest_framework import authentication
from rest_framework import exceptions


class SupabaseJWTAuthentication(authentication.BaseAuthentication):
    """Validate Supabase access tokens and map them to Django users."""

    keyword = "bearer"
    _jwks_client = None

    def authenticate(self, request):
        auth_header = authentication.get_authorization_header(request).decode("utf-8")
        if not auth_header:
            return None

        parts = auth_header.split()
        if len(parts) != 2 or parts[0].lower() != self.keyword:
            return None

        token = parts[1]
        payload = self._decode_token(token)
        user = self._get_or_create_user(payload)
        return (user, payload)

    def _decode_token(self, token):
        jwks_url = settings.SUPABASE_JWT_JWKS_URL
        if not jwks_url:
            raise exceptions.AuthenticationFailed(
                "SUPABASE_JWT_JWKS_URL is not configured"
            )

        issuer = settings.SUPABASE_JWT_ISSUER
        audience = settings.SUPABASE_JWT_AUDIENCE

        try:
            if self._jwks_client is None:
                self.__class__._jwks_client = jwt.PyJWKClient(jwks_url)

            signing_key = self._jwks_client.get_signing_key_from_jwt(token)
            payload = jwt.decode(
                token,
                signing_key.key,
                algorithms=["RS256"],
                audience=audience,
                issuer=issuer if issuer else None,
                options={"verify_iss": bool(issuer)},
            )
        except jwt.PyJWTError as exc:
            raise exceptions.AuthenticationFailed("Invalid Supabase JWT") from exc

        expected_role = getattr(settings, "SUPABASE_JWT_ROLE", "authenticated")
        if expected_role and payload.get("role") != expected_role:
            raise exceptions.AuthenticationFailed("Invalid Supabase JWT role")

        return payload

    def _get_or_create_user(self, payload):
        user_model = get_user_model()

        sub = payload.get("sub")
        email = payload.get("email")
        if not sub:
            raise exceptions.AuthenticationFailed("Token missing 'sub' claim")

        lookup = {"username": sub}
        defaults = {
            "email": email or "",
            "is_active": True,
        }

        user, created = user_model.objects.get_or_create(
            **lookup,
            defaults=defaults,
        )

        if not created and email and user.email != email:
            user.email = email
            user.save(update_fields=["email"])

        return user
