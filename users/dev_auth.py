"""
Development-only header authentication (X-Dev-User).

Disabled when DEBUG is False or ALLOW_DEV_HEADER_AUTH is False.
Never enable in production.
"""
from django.conf import settings
from django.contrib.auth import get_user_model


def get_dev_user(request):
    """
    Resolve user from X-Dev-User header. Returns None if disabled or missing.
    """
    if not getattr(settings, "DEBUG", False):
        return None
    if not getattr(settings, "ALLOW_DEV_HEADER_AUTH", True):
        return None
    username = request.headers.get("X-Dev-User")
    if not username:
        return None
    User = get_user_model()
    try:
        return User.objects.get(username=username)
    except User.DoesNotExist:
        return None
