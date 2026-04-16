"""Run value-score inference and persist SubscriptionValuation rows."""

from __future__ import annotations

import logging
import threading
from datetime import timedelta
from typing import Any

from django.db import close_old_connections, transaction
from django.utils import timezone

from gamification.services import award_points_for_subscription_valuation
from subscriptions.models import Subscription
from users.services.state_orchestrator import recompute_user_state

from ..models import Recommendation, SubscriptionValuation, ValuationContext, ValuationModelVersion
from .value_score_data import score_to_financial_fields
from .value_score_inference import ValueScoreModelNotAvailable, predict_user_subscriptions

logger = logging.getLogger(__name__)

TIER_LABELS = {1: "cold_start", 2: "xgboost", 3: "neural"}


def _get_or_create_model_version(*, name: str, version: str) -> ValuationModelVersion:
    obj, _ = ValuationModelVersion.objects.get_or_create(
        name=name,
        version=version,
        defaults={"description": "Value score model (platform_bundle)"},
    )
    return obj


def _recommendation_from_score(score: float, thresholds: dict[str, float]) -> str:
    if score >= thresholds.get("strong_buy_threshold", 127.5):
        return Recommendation.BUY
    if score >= thresholds.get("buy_threshold", 97.5):
        return Recommendation.BUY
    if score <= thresholds.get("skip_threshold", 52.5):
        return Recommendation.SKIP
    return Recommendation.WAIT


def _load_scoring_thresholds() -> dict[str, float]:
    from .value_score_paths import ensure_platform_bundle_importable
    from value_score_model.config import load_config

    ensure_platform_bundle_importable()
    cfg = load_config()
    return dict(cfg.get("recommendation", {}))


@transaction.atomic
def persist_subscription_value_scores(
    user_id: int,
    results_df,
    *,
    model_version: ValuationModelVersion,
    period_days: int = 30,
) -> list[SubscriptionValuation]:
    """Persist each row of the inference dataframe as SubscriptionValuation."""
    from django.contrib.auth import get_user_model

    User = get_user_model()
    user = User.objects.get(pk=user_id)
    thresholds = _load_scoring_thresholds()
    end = timezone.now().date()
    start = end - timedelta(days=period_days)

    created: list[SubscriptionValuation] = []
    for _, row in results_df.iterrows():
        sub_id = int(row["subscription_id"])
        try:
            sub = Subscription.objects.get(pk=sub_id, user_id=user_id)
        except Subscription.DoesNotExist:
            continue

        vs = int(row["value_score"])
        base_vs = int(row.get("base_value_score", vs))
        conf = float(row.get("confidence", 0.0))
        tier = int(row.get("tier_used", 0))
        tier_label = TIER_LABELS.get(tier, str(tier))

        total_cost, estimated_value, net_value = score_to_financial_fields(sub, vs, period_days=period_days)
        rec = _recommendation_from_score(float(vs), thresholds)

        evidence: dict[str, Any] = {
            "source": "value_score_model",
            "tier_used": tier_label,
            "tier_code": tier,
            "base_value_score": base_vs,
            "overutilisation_bonus": float(row.get("overutilisation_bonus", 0.0)),
            "feedback_numerics_included": False,
        }
        ev = row.get("evidence_json")
        if isinstance(ev, dict):
            evidence["features"] = ev
        elif isinstance(ev, list) and ev:
            evidence["features"] = ev

        obj, was_created = SubscriptionValuation.objects.update_or_create(
            user=user,
            subscription=sub,
            period_start=start,
            period_end=end,
            model_version=model_version,
            defaults={
                "context": ValuationContext.SUBSCRIPTION_RENEWAL,
                "total_cost": total_cost,
                "estimated_value": estimated_value,
                "net_value": net_value,
                "personal_value_score": min(max(vs, 0), 150),
                "base_value_score": min(max(base_vs, 0), 150),
                "tier_used": tier_label,
                "inference_status": "success",
                "feature_window_days": period_days,
                "recommendation": rec,
                "confidence": min(max(conf, 0.0), 1.0),
                "evidence_json": evidence,
                "explanation_json": {
                    "summary": f"Value score {vs} ({tier_label})",
                    "drivers": [],
                },
            },
        )
        created.append(obj)
        if was_created:
            try:
                award_points_for_subscription_valuation(obj)
            except Exception:
                pass

    return created


def run_value_scores_for_user(
    user_id: int,
    subscription_ids: list[int] | None = None,
    *,
    model_name: str = "value_score",
    model_version_str: str | None = None,
) -> dict[str, Any]:
    """
    End-to-end: predict + persist. Returns payload for API.

    `model_version_str` defaults to VALUE_SCORE_MODEL_VERSION setting or 'bundle'.
    """
    from django.conf import settings

    ver = model_version_str or getattr(settings, "VALUE_SCORE_MODEL_VERSION", "bundle")
    try:
        recompute_user_state(user_id)
        df = predict_user_subscriptions(user_id, subscription_ids=subscription_ids)
    except ValueScoreModelNotAvailable as e:
        return {"ok": False, "error": str(e), "valuations": []}

    if df is None or df.empty:
        return {"ok": True, "valuations": [], "message": "No subscriptions to score."}

    mv = _get_or_create_model_version(name=model_name, version=ver)
    vals = persist_subscription_value_scores(user_id, df, model_version=mv)
    from ..serializers import SubscriptionValuationSerializer

    ser = SubscriptionValuationSerializer(vals, many=True)
    return {"ok": True, "valuations": ser.data, "model_version": f"{model_name}@{ver}"}


def schedule_value_scores_for_user_after_commit(
    user_id: int,
    subscription_ids: list[int] | None = None,
) -> None:
    """
    Run value-score ML inference after the current DB transaction commits, in a
    background thread. Subscription create/update should not block the HTTP response
    on model load + inference (often multiple seconds).
    """

    ids = list(subscription_ids) if subscription_ids is not None else None

    def work() -> None:
        close_old_connections()
        try:
            run_value_scores_for_user(user_id, subscription_ids=ids)
        except Exception:
            logger.exception(
                "Background value score recomputation failed (user_id=%s subscription_ids=%s)",
                user_id,
                ids,
            )
        finally:
            close_old_connections()

    def after_commit() -> None:
        threading.Thread(target=work, name="value-score-recompute", daemon=True).start()

    transaction.on_commit(after_commit)
