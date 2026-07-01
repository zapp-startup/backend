"""Load trained ValueScoreModel from platform_bundle checkpoints (lazy singleton)."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from django.conf import settings

from .value_score_paths import ensure_platform_bundle_importable, value_score_checkpoint_dir


class ValueScoreModelNotAvailable(RuntimeError):
    """Checkpoint missing or model failed to load."""


@lru_cache(maxsize=1)
def _load_model():
    ensure_platform_bundle_importable()
    ckpt = value_score_checkpoint_dir()
    meta = ckpt / "meta.pkl"
    if not meta.is_file():
        raise ValueScoreModelNotAvailable(
            f"No trained value score checkpoint at {ckpt}. "
            "Train/export the model into this directory (meta.pkl, feature_engineer.pkl, tier*.pkl)."
        )
    from value_score_model.models.value_score_model import ValueScoreModel

    return ValueScoreModel.load(ckpt)


def get_value_score_model():
    """Return a fitted ValueScoreModel or raise ValueScoreModelNotAvailable."""
    if not getattr(settings, "VALUE_SCORE_ENABLED", True):
        raise ValueScoreModelNotAvailable("VALUE_SCORE_ENABLED is False.")
    return _load_model()


def clear_value_score_model_cache() -> None:
    _load_model.cache_clear()


def predict_user_subscriptions(user_id: int, subscription_ids: list[int] | None = None):
    """Run inference; returns pandas DataFrame of results."""
    import pandas as pd

    from .value_score_data import build_value_score_dataframes

    model = get_value_score_model()
    data = build_value_score_dataframes(user_id, subscription_ids=subscription_ids)
    if data["subscriptions"].empty:
        return pd.DataFrame()
    return model.predict(data)
