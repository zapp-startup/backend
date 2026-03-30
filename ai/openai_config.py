from django.conf import settings
from django.core.exceptions import ImproperlyConfigured


OPENAI_API_KEY_SETTING = "OPENAI_API_KEY"
MALFORMED_ENV_MARKER = "OPENAI_"


def get_openai_api_key() -> str | None:
    """Return the configured OpenAI API key without logging or exposing it."""
    key = (getattr(settings, OPENAI_API_KEY_SETTING, "") or "").strip()
    return key or None


def get_openai_api_key_issue(key: str | None = None) -> str | None:
    """Classify common local configuration problems without exposing the key."""
    normalized_key = (key or getattr(settings, OPENAI_API_KEY_SETTING, "") or "").strip()
    if not normalized_key:
        return "missing"

    if any(char.isspace() for char in normalized_key):
        return "malformed_whitespace"

    # A frequent .env editing mistake is accidentally appending another env var
    # name onto the same OPENAI_API_KEY line.
    if MALFORMED_ENV_MARKER in normalized_key[len("sk-") :]:
        return "malformed_env_value"

    return None


def require_openai_api_key() -> str:
    """Return the OpenAI API key or raise a safe configuration error."""
    key = get_openai_api_key()
    issue = get_openai_api_key_issue(key)
    if issue is None and key:
        return key

    if issue == "missing":
        raise ImproperlyConfigured(
            "OPENAI_API_KEY is not configured. Set it in the server environment before enabling OpenAI calls."
        )

    raise ImproperlyConfigured(
        "OPENAI_API_KEY appears malformed. Check the server environment and ensure the value contains only the API key."
    )
