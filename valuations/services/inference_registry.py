from __future__ import annotations

from .value_score_inference import get_value_score_model


def get_default_value_score_model():
    return get_value_score_model()
