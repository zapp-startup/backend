from django.conf import settings
from django.core.exceptions import ImproperlyConfigured


OPENAI_API_KEY_SETTING = "OPENAI_API_KEY"


def get_openai_api_key() -> str | None:
    """Return the configured OpenAI API key without logging or exposing it."""
    key = (getattr(settings, OPENAI_API_KEY_SETTING, "") or "").strip()
    return key or None


def require_openai_api_key() -> str:
    """Return the OpenAI API key or raise a safe configuration error."""
    key = get_openai_api_key()
    if key:
        return key

    raise ImproperlyConfigured(
        "OPENAI_API_KEY is not configured. Set it in the server environment before enabling OpenAI calls."
    )