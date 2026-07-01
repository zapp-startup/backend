from __future__ import annotations

from datetime import timedelta

from django.utils import timezone


def is_stale_timestamp(value, *, max_age_hours: int = 24) -> bool:
    if value is None:
        return True
    return value <= timezone.now() - timedelta(hours=max_age_hours)
