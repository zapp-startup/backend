from __future__ import annotations

from datetime import datetime, time, timedelta
from decimal import Decimal
import json
from typing import Any

import pandas as pd
from django.conf import settings
from django.db import transaction as db_transaction
from django.utils import timezone

from banking.models import BankTransaction
from banking.categories import ZappPrimaryCategory
from subscriptions.models import Merchant
from subscriptions.models import MerchantCategory
from transactions.models import PaymentChannel, Transaction, TransactionCategory, TransactionDirection
from transactions.services.events import mark_transaction_dirty
from users.models import UserComputed, UserRawExplicit, UserRawInferred
from users.services.state_orchestrator import recompute_user_state

from ..models import TransactionValuation, ValuationContext, ValuationModelVersion
from .persistence import create_transaction_valuation_snapshot
from .value_score_inference import ValueScoreModelNotAvailable, get_value_score_model
from .value_score_orchestrator import _get_or_create_model_version


TRANSACTION_MODEL_NAME = "value_score"


def _print_transaction_model_payload(transaction: Transaction, data: dict[str, pd.DataFrame]) -> None:
    print("")
    print("=== TRANSACTION VALUE SCORE MODEL INPUT ===")
    print(f"transaction_id={transaction.id} user_id={transaction.user_id} description={transaction.description_raw!r}")
    for key, df in data.items():
        print(f"-- {key} --")
        if df.empty:
            print("[]")
            continue
        print(json.dumps(df.to_dict(orient="records"), indent=2, default=str))
    print("=== END TRANSACTION VALUE SCORE MODEL INPUT ===")
    print("")


def _merchant_category_from_transaction(category: str) -> str:
    """Map TransactionCategory to MerchantCategory for domain logic and display."""
    mapping = {
        TransactionCategory.SUBSCRIPTIONS: MerchantCategory.STREAMING,
        TransactionCategory.GROCERIES: MerchantCategory.GROCERY,
        TransactionCategory.EATING_OUT: MerchantCategory.FOOD,
        TransactionCategory.BILLS: MerchantCategory.UTILITIES,
        # HEALTH uses MerchantCategory.HEALTH (not OTHER) so app logic retains the signal.
        # The ML model vocab does not include "health"; see _model_safe_merchant_category.
        TransactionCategory.HEALTH: MerchantCategory.HEALTH,
        TransactionCategory.EDUCATION: MerchantCategory.EDUCATION,
        TransactionCategory.SHOPPING: MerchantCategory.OTHER,
        TransactionCategory.TRANSPORT: MerchantCategory.OTHER,
        TransactionCategory.ENTERTAINMENT: MerchantCategory.OTHER,
    }
    return mapping.get(category, MerchantCategory.OTHER)


# ML model's MERCHANT_CATEGORY_VOCAB (must stay in sync with
# value_score_model/pipeline/feature_engineering.py:MERCHANT_CATEGORY_VOCAB).
_MODEL_MERCHANT_CATEGORY_VOCAB = frozenset([
    "streaming", "grocery", "fitness", "software",
    "utilities", "food", "education", "other",
])

_MODEL_MERCHANT_CATEGORY_FALLBACK: dict[str, str] = {
    # "health" is not in the model vocab; map to "other" at the model boundary
    # to avoid the unknown-category (index 0) encoding.
    MerchantCategory.HEALTH: MerchantCategory.OTHER,
}


def _model_safe_merchant_category(category: str) -> str:
    """
    Return a merchant category that is safe to pass to the ML model.

    App-domain categories (e.g. MerchantCategory.HEALTH) are richer than what
    the trained model's MERCHANT_CATEGORY_VOCAB knows.  This function maps any
    unknown category to the closest known one so the model does not silently
    receive an out-of-vocabulary value (which would encode as index 0).

    Call this ONLY at the model input boundary (merchants_df / transaction_entities).
    Everywhere else, use the richer domain category.
    """
    cat = (category or "other").lower()
    if cat in _MODEL_MERCHANT_CATEGORY_VOCAB:
        return cat
    return _MODEL_MERCHANT_CATEGORY_FALLBACK.get(cat, MerchantCategory.OTHER)


def _normalize_merchant_name(name: str) -> str:
    """
    Normalise a raw merchant description for stable identity.
    Lowercases, strips trailing noise (store numbers, extra spaces) and
    collapses internal whitespace so that "CVS Pharmacy #1234" and
    "CVS Pharmacy" resolve to the same canonical string.
    """
    import re
    if not name:
        return ""
    s = name.strip().lower()
    # Remove trailing store/branch numbers like "#1234" or " 1234"
    s = re.sub(r"\s*#\d+\s*$", "", s)
    s = re.sub(r"\s{2,}", " ", s)
    return s[:255]


def _row_explicit(user_id: int, explicit: UserRawExplicit | None) -> dict[str, Any]:
    if not explicit:
        return {
            "user_id": user_id,
            "life_stage": "other",
            "financial_goal": "other",
            "monthly_income": 3000.0,
            "value_priority_cost": None,
            "value_priority_quality": None,
            "value_priority_sustainability": None,
            "risk_tolerance": "medium",
            "budget_style": "flexible",
        }
    return {
        "user_id": user_id,
        "life_stage": (explicit.life_stage or "other").lower(),
        "financial_goal": (explicit.financial_goal or "other").lower(),
        "monthly_income": float(explicit.monthly_income) if explicit.monthly_income is not None else 3000.0,
        "value_priority_cost": float(explicit.value_priority_cost) if explicit.value_priority_cost is not None else None,
        "value_priority_quality": float(explicit.value_priority_quality) if explicit.value_priority_quality is not None else None,
        "value_priority_sustainability": (
            float(explicit.value_priority_sustainability) if explicit.value_priority_sustainability is not None else None
        ),
        "risk_tolerance": (explicit.risk_tolerance or "medium").lower(),
        "budget_style": (explicit.budget_style or "flexible").lower(),
    }


def _row_computed(user_id: int, computed: UserComputed | None) -> dict[str, Any]:
    if not computed:
        return {
            "user_id": user_id,
            "cost_weight": None,
            "quality_weight": None,
            "sustainability_weight": None,
            "impulse_susceptibility_score": None,
            "regret_sensitivity": None,
            "budget_adherence_score": None,
        }
    return {
        "user_id": user_id,
        "cost_weight": computed.cost_weight,
        "quality_weight": computed.quality_weight,
        "sustainability_weight": computed.sustainability_weight,
        "impulse_susceptibility_score": computed.impulse_susceptibility_score,
        "regret_sensitivity": computed.regret_sensitivity,
        "budget_adherence_score": computed.budget_adherence_score,
    }


def _row_inferred(user_id: int, inferred: UserRawInferred | None) -> dict[str, Any]:
    if not inferred:
        return {
            "user_id": user_id,
            "percent_income_spent_on_subscriptions": None,
            "regret_frequency": None,
            "active_subscriptions_count": None,
        }
    return {
        "user_id": user_id,
        "percent_income_spent_on_subscriptions": inferred.percent_income_spent_on_subscriptions,
        "regret_frequency": inferred.regret_frequency,
        "active_subscriptions_count": float(inferred.active_subscriptions_count)
        if inferred.active_subscriptions_count is not None
        else None,
    }


def build_transaction_value_score_dataframes(transaction: Transaction) -> tuple[dict[str, pd.DataFrame], int]:
    include_tx_feedback = bool(getattr(settings, "VALUE_SCORE_INCLUDE_FEEDBACK_NUMERICS", False))
    user_id = transaction.user_id
    recompute_user_state(user_id)
    explicit = UserRawExplicit.objects.filter(user_id=user_id).first()
    computed = UserComputed.objects.filter(user_id=user_id).first()
    inferred = UserRawInferred.objects.filter(user_id=user_id).first()

    transaction_id = int(transaction.id)
    synthetic_merchant_id = transaction.merchant_id or -transaction_id
    merchant_name = ""
    if transaction.merchant_id and transaction.merchant:
        merchant_name = transaction.merchant.name
    else:
        raw_name = transaction.description_raw or f"Transaction {transaction.id}"
        # Normalise before get_or_create so that superficial text variations
        # (e.g. "CVS Pharmacy #1234" vs "CVS Pharmacy") resolve to the same record.
        merchant_name = _normalize_merchant_name(raw_name)
        domain_category = _merchant_category_from_transaction(transaction.category).lower()
        merchant, _ = Merchant.objects.get_or_create(
            name=merchant_name,
            defaults={"category": domain_category},
        )
        if transaction.merchant_id != merchant.id:
            transaction.merchant = merchant
            transaction.save(update_fields=["merchant"])
            synthetic_merchant_id = merchant.id

    # Use model-safe category in the DataFrame passed to the ML model.
    # The richer domain category (e.g. "health") is stored on the Merchant record;
    # here we translate it to the nearest model-vocabulary term.
    domain_cat = _merchant_category_from_transaction(transaction.category).lower()
    model_cat = _model_safe_merchant_category(domain_cat)

    merchants_df = pd.DataFrame(
        [
            {
                "id": synthetic_merchant_id,
                "name": merchant_name,
                "category": model_cat,
            }
        ]
    )

    tx_queryset = Transaction.objects.filter(user_id=user_id, direction=TransactionDirection.SPEND)
    if transaction.merchant_id:
        tx_queryset = tx_queryset.filter(merchant_id=transaction.merchant_id)
    else:
        tx_queryset = tx_queryset.filter(id=transaction.id)

    tx_rows: list[dict[str, Any]] = []
    for tx in tx_queryset.order_by("occurred_at"):
        merchant_id = tx.merchant_id or synthetic_merchant_id
        row = {
            "id": tx.id,
            "user_id": user_id,
            "merchant_id": merchant_id,
            "occurred_at": tx.occurred_at.isoformat(),
            "amount": float(tx.amount),
            "direction": tx.direction,
        }
        if include_tx_feedback:
            row.update(
                {
                    "satisfaction_rating": tx.satisfaction_rating,
                    "regret_rating": tx.regret_rating,
                    "repurchase_likelihood": tx.repurchase_likelihood,
                    "impulse_score": tx.impulse_score,
                    "regret_score": tx.regret_score,
                    "usage_frequency": tx.usage_frequency,
                }
            )
        tx_rows.append(row)

    # impulse_score, regret_score, feedback_value_score, feedback_confidence are
    # not collected by the live feedback flow; they are null unless explicitly
    # set by a backend derivation service. Pass null (not 0) so the model's
    # _safe_fill uses its configured defaults rather than receiving a false zero.
    transaction_entities = pd.DataFrame(
        [
            {
                "transaction_id": transaction_id,
                "user_id": user_id,
                "merchant_id": synthetic_merchant_id,
                "occurred_at": transaction.occurred_at.isoformat(),
                "amount": float(transaction.amount),
                "category": transaction.category,
                # Validate usage_frequency: must be a positive integer if present.
                # 0 or negative values are treated as missing to avoid misleading signals.
                "usage_frequency": float(transaction.usage_frequency)
                    if transaction.usage_frequency is not None and transaction.usage_frequency > 0
                    else None,
                "satisfaction_rating": transaction.satisfaction_rating,
                "regret_rating": transaction.regret_rating,
                "repurchase_likelihood": transaction.repurchase_likelihood,
                # Backend-computed; null when not yet derived.
                "impulse_score": transaction.impulse_score,
                "regret_score": transaction.regret_score,
                # Backend-derived from ratings; null when ratings absent.
                "feedback_value_score": transaction.feedback_value_score,
                "feedback_confidence": transaction.feedback_confidence,
                # Model-safe merchant_category (maps app-domain categories to vocab).
                "merchant_category": model_cat,
            }
        ]
    )

    feedback_signals = pd.DataFrame(
        [
            {
                "transaction_id": transaction_id,
                "user_id": user_id,
                "merchant_id": synthetic_merchant_id,
                "feedback_value_score": transaction.feedback_value_score,
                "feedback_confidence": transaction.feedback_confidence,
            }
        ]
    )

    # FeatureEngineer requires `subscriptions` (same schema as subscription scoring).
    # One-off transactions use a synthetic row; id matches transaction_id so
    # predict() output rows align with predict_transaction_value_score() lookup.
    if transaction.occurred_at:
        aware = (
            transaction.occurred_at
            if timezone.is_aware(transaction.occurred_at)
            else timezone.make_aware(transaction.occurred_at, timezone.utc)
        )
        started_on = aware.isoformat()
    else:
        started_on = None

    subscriptions_df = pd.DataFrame(
        [
            {
                "id": transaction_id,
                "user_id": user_id,
                "merchant_id": synthetic_merchant_id,
                "price": float(transaction.amount),
                "started_on": started_on,
                "billing_cycle": "monthly",
                "status": "active",
                "usage_frequency": (
                    float(transaction.usage_frequency)
                    if transaction.usage_frequency is not None and transaction.usage_frequency > 0
                    else 1.0
                ),
                "reactivation_count": 0,
                "subscription_utilization": None,
                "subscription_cost_benefit": None,
            }
        ]
    )

    data = {
        "merchants": merchants_df,
        "subscriptions": subscriptions_df,
        "transactions": pd.DataFrame(tx_rows),
        "transaction_entities": transaction_entities,
        "user_explicit": pd.DataFrame([_row_explicit(user_id, explicit)]),
        "user_computed": pd.DataFrame([_row_computed(user_id, computed)]),
        "user_inferred": pd.DataFrame([_row_inferred(user_id, inferred)]),
        "feedback_signals": feedback_signals,
    }
    return data, transaction_id


def predict_transaction_value_score(transaction: Transaction) -> dict[str, Any]:
    model = get_value_score_model()
    data, transaction_id = build_transaction_value_score_dataframes(transaction)
    _print_transaction_model_payload(transaction, data)
    result_df = None
    predict_transactions = getattr(model, "predict_transactions", None)
    if callable(predict_transactions):
        result_df = predict_transactions(data)
    if not isinstance(result_df, pd.DataFrame):
        result_df = model.predict(data)
    if result_df is None or result_df.empty:
        raise ValueScoreModelNotAvailable("Value score model returned no transaction score.")

    if "transaction_id" in result_df.columns:
        match = result_df[result_df["transaction_id"] == transaction_id]
    else:
        match = result_df[result_df["subscription_id"] == transaction_id]
    row = match.iloc[0] if not match.empty else result_df.iloc[0]
    value_score = int(row["value_score"])
    base_value_score = int(row.get("base_value_score", value_score))
    confidence = float(row.get("confidence", 0.0))
    raw_tier = row.get("tier_used", 0)
    tier_used = str(raw_tier)
    evidence = row.get("evidence_json")
    return {
        "value_score": min(max(value_score, 0), 150),
        "base_value_score": min(max(base_value_score, 0), 150),
        "confidence": min(max(confidence, 0.0), 1.0),
        "tier_used": tier_used,
        "evidence_json": evidence if isinstance(evidence, (dict, list)) else None,
    }


@db_transaction.atomic
def persist_transaction_value_score(transaction: Transaction) -> TransactionValuation:
    version = getattr(settings, "VALUE_SCORE_MODEL_VERSION", "bundle")
    model_version: ValuationModelVersion = _get_or_create_model_version(name=TRANSACTION_MODEL_NAME, version=version)
    prediction = predict_transaction_value_score(transaction)
    valuation = create_transaction_valuation_snapshot(
        transaction=transaction,
        model_version=model_version,
        context=ValuationContext.ONE_OFF_PURCHASE,
        value_score=prediction["value_score"],
        base_value_score=prediction["base_value_score"],
        confidence=prediction["confidence"],
        tier_used=prediction["tier_used"],
        inference_status="success",
        evidence_json=prediction["evidence_json"],
        reasoning_json={"summary": f"Transaction value score {prediction['value_score']}"},
    )
    transaction.personal_value_score = prediction["value_score"]
    transaction.value_score_confidence = prediction["confidence"]
    transaction.value_score_model_version = f"{model_version.name}@{model_version.version}"
    transaction.value_score_computed_at = timezone.now()
    transaction.save(
        update_fields=[
            "personal_value_score",
            "value_score_confidence",
            "value_score_model_version",
            "value_score_computed_at",
        ]
    )
    return valuation


_ZAPP_TO_TRANSACTION_CATEGORY: dict[str, str] = {
    ZappPrimaryCategory.GROCERIES_ESSENTIALS: TransactionCategory.GROCERIES,
    ZappPrimaryCategory.DINING_CAFES: TransactionCategory.EATING_OUT,
    ZappPrimaryCategory.TRANSPORTATION: TransactionCategory.TRANSPORT,
    ZappPrimaryCategory.TRAVEL: TransactionCategory.TRANSPORT,
    ZappPrimaryCategory.SHOPPING: TransactionCategory.SHOPPING,
    ZappPrimaryCategory.SUBSCRIPTIONS: TransactionCategory.SUBSCRIPTIONS,
    ZappPrimaryCategory.ENTERTAINMENT_SOCIAL: TransactionCategory.ENTERTAINMENT,
    ZappPrimaryCategory.HEALTH_WELLNESS: TransactionCategory.HEALTH,
    ZappPrimaryCategory.EDUCATION_CAREER: TransactionCategory.EDUCATION,
    ZappPrimaryCategory.UTILITIES_BILLS: TransactionCategory.BILLS,
    ZappPrimaryCategory.HOUSING_LIVING: TransactionCategory.BILLS,
}


def _zapp_category_to_transaction_category(zapp_primary: str) -> str:
    """
    Map a ZappPrimaryCategory value to the closest TransactionCategory.
    Falls back to OTHER when there is no meaningful mapping (e.g. INCOME, FINANCIAL_TRANSFERS).
    """
    return _ZAPP_TO_TRANSACTION_CATEGORY.get(zapp_primary, TransactionCategory.OTHER)


@db_transaction.atomic
def ensure_bank_feedback_transaction(bank_transaction: BankTransaction) -> Transaction:
    occurred_at = timezone.make_aware(datetime.combine(bank_transaction.date, time(hour=12)))
    # Preserve the Zapp category signal rather than always defaulting to OTHER.
    category = _zapp_category_to_transaction_category(bank_transaction.zapp_primary_category or "")
    defaults = {
        "user": bank_transaction.user,
        "merchant": None,
        "subscription": None,
        "description_raw": bank_transaction.merchant_name or bank_transaction.name,
        "amount": Decimal(str(abs(bank_transaction.amount))),
        "currency": bank_transaction.iso_currency_code or "USD",
        "occurred_at": occurred_at,
        "direction": TransactionDirection.SPEND if float(bank_transaction.amount) >= 0 else TransactionDirection.INCOME,
        "category": category,
        "payment_channel": PaymentChannel.CARD,
    }
    obj, _ = Transaction.objects.update_or_create(bank_transaction=bank_transaction, defaults=defaults)
    mark_transaction_dirty(obj, reason="bank_transaction_sync", priority=4)
    return obj
