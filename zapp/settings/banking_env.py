"""
Helpers for reading banking policy flags from the environment.

Used by development settings so local defaults stay relaxed while allowing
explicit .env overrides for strict Plaid / consent testing.
"""
from __future__ import annotations

import os


def env_bool(name: str, default: bool) -> bool:
    v = os.getenv(name)
    if v is None:
        return default
    return v.strip().lower() in ("1", "true", "yes")
