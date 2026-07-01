from __future__ import annotations

import json
import logging
import sys
from typing import Any

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

logger = logging.getLogger(__name__)


def _configured_keys(setting_single: str, setting_multi: str) -> list[str]:
    csv_value = (getattr(settings, setting_multi, "") or "").strip()
    keys = [part.strip() for part in csv_value.split(",") if part.strip()]
    if keys:
        return keys

    single_key = (getattr(settings, setting_single, "") or "").strip()
    return [single_key] if single_key else []


def _fernet_instances(setting_single: str, setting_multi: str) -> list[Fernet]:
    instances: list[Fernet] = []
    for key in _configured_keys(setting_single, setting_multi):
        try:
            instances.append(Fernet(key.encode("ascii")))
        except Exception as exc:
            logger.error("Invalid encryption key for %s/%s: %s", setting_single, setting_multi, exc)
    return instances


def app_data_fernets() -> list[Fernet]:
    return _fernet_instances("APP_DATA_ENCRYPTION_KEY", "APP_DATA_ENCRYPTION_KEYS")


def has_app_data_encryption_enabled() -> bool:
    return bool(app_data_fernets())


def app_data_encryption_required() -> bool:
    settings_module = str(getattr(settings, "SETTINGS_MODULE", "") or getattr(settings, "DJANGO_SETTINGS_MODULE", ""))
    if settings_module.endswith(".test"):
        return False
    if any(arg == "test" for arg in sys.argv):
        return False
    return True


def require_app_data_encryption() -> None:
    if not has_app_data_encryption_enabled() and app_data_encryption_required():
        raise ImproperlyConfigured(
            "APP_DATA_ENCRYPTION_KEY or APP_DATA_ENCRYPTION_KEYS must be configured outside tests."
        )


def encrypt_app_data(plaintext: str) -> str:
    if plaintext == "":
        return plaintext
    fernets = app_data_fernets()
    if not fernets:
        require_app_data_encryption()
        return plaintext
    return fernets[0].encrypt(plaintext.encode("utf-8")).decode("ascii")


def decrypt_app_data(stored: str) -> tuple[str, bool]:
    if stored == "":
        return stored, False

    fernets = app_data_fernets()
    if not fernets:
        require_app_data_encryption()
        return stored, False

    for fernet in fernets:
        try:
            return fernet.decrypt(stored.encode("ascii")).decode("utf-8"), True
        except (InvalidToken, ValueError, TypeError):
            continue

    # Legacy plaintext before encryption was enabled/backfilled.
    return stored, False


REDACTED = "[REDACTED]"
REDACT_KEYS = (
    "password",
    "token",
    "secret",
    "authorization",
    "cookie",
    "session",
    "access_token",
    "refresh_token",
    "public_token",
    "consent_text",
    "content",
    "reflection_text",
)


def _is_redacted_key(key: str) -> bool:
    normalized = key.lower()
    return any(candidate in normalized for candidate in REDACT_KEYS)


def redact_sensitive_value(value: Any, depth: int = 0) -> Any:
    if value is None:
        return value
    if depth > 4:
        return "[Truncated]"
    if isinstance(value, str):
        return value[:256] + ("...[truncated]" if len(value) > 256 else "")
    if isinstance(value, list):
        return [redact_sensitive_value(item, depth + 1) for item in value[:20]]
    if isinstance(value, dict):
        sanitized: dict[str, Any] = {}
        for key, nested in value.items():
            sanitized[str(key)] = REDACTED if _is_redacted_key(str(key)) else redact_sensitive_value(nested, depth + 1)
        return sanitized
    return value


def redact_error_message(message: str) -> str:
    if not message:
        return ""
    try:
        parsed = json.loads(message)
    except ValueError:
        return str(redact_sensitive_value(message))
    return json.dumps(redact_sensitive_value(parsed), ensure_ascii=True)
