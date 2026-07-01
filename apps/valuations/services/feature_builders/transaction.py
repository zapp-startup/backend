from __future__ import annotations

from apps.valuations.services.feature_contracts import FeatureBundle
from apps.valuations.services.transaction_value_score import build_transaction_value_score_dataframes


def build_transaction_feature_bundle(transaction) -> FeatureBundle:
    data, entity_id = build_transaction_value_score_dataframes(transaction)
    return FeatureBundle(data=data, entity_id=entity_id)
