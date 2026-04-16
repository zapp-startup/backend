from __future__ import annotations

from django.utils import timezone


def serialize_dt(value):
    if value is None:
        return None
    if timezone.is_aware(value):
        return value.isoformat()
    return timezone.make_aware(value, timezone.utc).isoformat()
