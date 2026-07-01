from django.conf import settings
from django.contrib.auth import get_user_model
from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import AuthenticationFailed


class DebugHeaderAuthentication(BaseAuthentication):
    """
    Development-only authenticator that trusts X-Dev-User.

    This stays disabled outside DEBUG so production traffic must use the normal
    authentication flow.
    """

    header_name = "X-Dev-User"

    def authenticate(self, request):
        if not settings.DEBUG:
            return None

        username = (request.headers.get(self.header_name) or "").strip()
        if not username:
            return None

        User = get_user_model()
        try:
            user = User.objects.get(username=username)
        except User.DoesNotExist as exc:
            raise AuthenticationFailed("Invalid X-Dev-User header.") from exc

        return (user, None)
