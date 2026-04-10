"""Resolve paths to the local `value_score_model` package and checkpoints."""

from __future__ import annotations

import sys
from pathlib import Path

from django.conf import settings


def platform_bundle_root() -> Path:
    """Repository root for the local `value_score_model` project."""
    override = getattr(settings, "VALUE_SCORE_BUNDLE_PATH", None)
    if override:
        return Path(override).resolve()
    return (Path(settings.BASE_DIR).parent / "value_score_model").resolve()


def value_score_checkpoint_dir() -> Path:
    """Directory with meta.pkl, feature_engineer.pkl, tier1/2 .pkl, tier3 .pt (flat layout)."""
    override = getattr(settings, "VALUE_SCORE_CHECKPOINT_DIR", None)
    if override:
        return Path(override).resolve()
    return platform_bundle_root() / "checkpoints"


def ensure_platform_bundle_importable() -> Path:
    """Insert the project parent on sys.path so `import value_score_model` works."""
    root = platform_bundle_root()
    s = str(root.parent)
    if s not in sys.path:
        sys.path.insert(0, s)
    return root
