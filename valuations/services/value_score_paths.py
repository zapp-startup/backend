"""Resolve paths to the local `platform_bundle` value-score package and checkpoints."""

from __future__ import annotations

import sys
from pathlib import Path

from django.conf import settings


def platform_bundle_root() -> Path:
    """Directory that contains the `value_score_model` package (parent is on sys.path)."""
    override = getattr(settings, "VALUE_SCORE_BUNDLE_PATH", None)
    if override:
        return Path(override).resolve()
    # backend/zapp/settings → parents[2] is backend; project root is one level up from backend
    return (Path(settings.BASE_DIR).parent / "platform_bundle").resolve()


def value_score_checkpoint_dir() -> Path:
    """Directory with meta.pkl, feature_engineer.pkl, tier1/2 .pkl, tier3 .pt (flat layout)."""
    override = getattr(settings, "VALUE_SCORE_CHECKPOINT_DIR", None)
    if override:
        return Path(override).resolve()
    # Artifacts live directly under platform_bundle/checkpoints/ (not checkpoints/value_score_model/)
    return platform_bundle_root() / "checkpoints"


def ensure_platform_bundle_importable() -> Path:
    """Insert platform_bundle on sys.path so `import value_score_model` works."""
    root = platform_bundle_root()
    s = str(root)
    if s not in sys.path:
        sys.path.insert(0, s)
    return root
