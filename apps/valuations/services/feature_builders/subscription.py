from __future__ import annotations

from apps.valuations.services.feature_contracts import FeatureBundle
from apps.valuations.services.value_score_data import build_value_score_dataframes


def build_subscription_feature_bundle(user_id: int, *, subscription_ids: list[int] | None = None) -> FeatureBundle:
    return FeatureBundle(data=build_value_score_dataframes(user_id, subscription_ids=subscription_ids), entity_id=user_id)
