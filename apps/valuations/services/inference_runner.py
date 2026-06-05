from __future__ import annotations

from .inference_registry import get_default_value_score_model


def run_model_predict(data):
    model = get_default_value_score_model()
    return model.predict(data)
