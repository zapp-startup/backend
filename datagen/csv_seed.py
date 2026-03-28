from __future__ import annotations

import csv
import json
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Callable

from django.contrib.auth import get_user_model
from django.utils import timezone

from ai.models import Conversation, Message, UserFact
from datagen.agents.anomaly import AnomalyAgent
from datagen.agents.audit import AuditAgent
from datagen.agents.behavior import BehaviorAgent
from datagen.agents.conversation import ConversationAgent
from datagen.agents.income import IncomeAgent
from datagen.agents.merchant import MerchantAgent
from datagen.agents.obligation import ObligationAgent
from datagen.agents.persona import PersonaAgent
from datagen.agents.spend import SpendAgent
from datagen.agents.subscription import SubscriptionAgent
from datagen.agents.valuation import ValuationAgent
from datagen.distributions import make_rng
from datagen.pipeline import _apply_monthly_debt_transitions, _build_faker
from datagen.state import UserState
from datagen.text import reset_conversation_dedup
from subscriptions.models import Merchant, Subscription
from transactions.models import Transaction
from users.models import UserComputed, UserPreference, UserRawExplicit, UserRawInferred
from valuations.models import ItemValuation, SubscriptionValuation, ValuationModelVersion

User = get_user_model()
SENSITIVE_FIELDS = {"password", "plaid_access_token"}


def _json_default(value):
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def _csv_value(value):
    if value is None:
        return None
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (dict, list)):
        return json.dumps(value, default=_json_default, sort_keys=True)
    return value


def _get_csv_fields(model_class):
    fields = []
    for field in model_class._meta.get_fields():
        if field.many_to_many or field.one_to_many:
            continue
        if field.name in SENSITIVE_FIELDS:
            continue
        if not getattr(field, "concrete", False):
            continue
        if hasattr(field, "attname") and hasattr(field, "remote_field") and field.remote_field:
            fields.append(field.attname)
        else:
            fields.append(field.name)
    return fields


def _row_for_fields(row: dict, fields: list[str]):
    return [_csv_value(row.get(field_name)) for field_name in fields]


def _write_csv(output_path: Path, model_class, rows: list[dict], filename: str):
    path = output_path / filename
    fields = _get_csv_fields(model_class)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(fields)
        for row in rows:
            writer.writerow(_row_for_fields(row, fields))


def _build_user_row(user_id: int, username: str, email: str, faker, password: str, now: datetime):
    user = User(
        id=user_id,
        username=username,
        email=email,
        first_name=faker.first_name(),
        last_name=faker.last_name(),
        is_staff=False,
        is_active=True,
        is_superuser=False,
        last_login=None,
        date_joined=now,
        supabase_uid=None,
    )
    user.set_password(password)
    return {
        "id": user.id,
        "last_login": user.last_login,
        "is_superuser": user.is_superuser,
        "username": user.username,
        "first_name": user.first_name,
        "last_name": user.last_name,
        "email": user.email,
        "is_staff": user.is_staff,
        "is_active": user.is_active,
        "date_joined": user.date_joined,
        "supabase_uid": user.supabase_uid,
    }


def _build_preferences_rows(user_id: int, state: UserState, rng, now: datetime, start_id: int):
    prefs = {
        "values_convenience": {
            "value_type": "bool",
            "value_json": {"value": state.quality_preference < 0.4},
        },
        "subscription_sensitivity": {
            "value_type": "int",
            "value_json": {"value": int(state.credit_stress * 10)},
        },
        "monthly_budget_target": {
            "value_type": "float",
            "value_json": {"value": float(state.monthly_income) * (0.7 + state.budget_adherence * 0.2)},
        },
    }

    rows = []
    next_id = start_id
    for key, value in prefs.items():
        rows.append(
            {
                "id": next_id,
                "user_id": user_id,
                "key": key,
                "value_type": value["value_type"],
                "value_json": value["value_json"],
                "source": "manual",
                "confidence": round(0.7 + float(rng.random()) * 0.3, 2),
                "updated_at": now,
            }
        )
        next_id += 1
    return rows, next_id


def _build_subscriptions_rows(user_id: int, subscription_dicts: list[dict], merchant_rows_by_name: dict, now: datetime, start_id: int):
    rows = []
    next_id = start_id
    active_merchant_ids = set()
    for sub in subscription_dicts:
        merchant_name = sub.get("merchant_info", {}).get("name", "")
        merchant_row = merchant_rows_by_name.get(merchant_name)
        if not merchant_row:
            continue

        status = sub.get("status", "active")
        if status == "active":
            merchant_id = merchant_row["id"]
            if merchant_id in active_merchant_ids:
                status = "paused"
            else:
                active_merchant_ids.add(merchant_id)

        rows.append(
            {
                "id": next_id,
                "user_id": user_id,
                "merchant_id": merchant_row["id"],
                "merchant_category": merchant_row["category"],
                "plan_name": sub.get("plan_name"),
                "status": status,
                "billing_cycle": sub.get("billing_cycle", "monthly"),
                "price": sub.get("price", Decimal("9.99")),
                "currency": "USD",
                "started_on": sub.get("started_on"),
                "renewal_date": sub.get("renewal_date"),
                "cancelled_on": sub.get("cancelled_on"),
                "notes": sub.get("notes"),
                "usage_frequency": sub.get("usage_frequency"),
                "reactivation_count": sub.get("reactivation_count", 0),
                "feedback_value_score": None,
                "feedback_confidence": None,
                "subscription_utilization": None,
                "subscription_cost_benefit": None,
                "created_at": now,
                "updated_at": now,
            }
        )
        next_id += 1
    return rows, next_id


def _build_transactions_rows(user_id: int, transaction_dicts: list[dict], merchant_rows_by_name: dict, subscription_rows_by_merchant: dict, now: datetime, start_id: int):
    rows = []
    next_id = start_id
    for txn in transaction_dicts:
        merchant_name = (txn.get("merchant_info") or {}).get("name")
        merchant_row = merchant_rows_by_name.get(merchant_name) if merchant_name else None
        subscription_row = subscription_rows_by_merchant.get(merchant_name) if merchant_name else None
        rows.append(
            {
                "id": next_id,
                "user_id": user_id,
                "merchant_id": merchant_row["id"] if merchant_row else None,
                "subscription_id": subscription_row["id"] if subscription_row else None,
                "direction": txn.get("direction", "spend"),
                "amount": abs(txn["amount"]),
                "currency": "USD",
                "occurred_at": txn["occurred_at"],
                "category": txn.get("category", "other"),
                "payment_channel": txn.get("payment_channel", "card"),
                "description_raw": txn.get("description_raw"),
                "satisfaction_rating": txn.get("satisfaction_rating"),
                "regret_rating": txn.get("regret_rating"),
                "repurchase_likelihood": txn.get("repurchase_likelihood"),
                "usage_frequency": txn.get("usage_frequency"),
                "reflection_text": txn.get("reflection_text"),
                "considered_at": txn.get("considered_at"),
                "used_buy_advisor": txn.get("used_buy_advisor", False),
                "self_report_researched": txn.get("self_report_researched"),
                "impulse_score": txn.get("impulse_score"),
                "regret_score": txn.get("regret_score"),
                "feedback_value_score": txn.get("feedback_value_score"),
                "feedback_confidence": txn.get("feedback_confidence"),
                "created_at": now,
            }
        )
        next_id += 1
    return rows, next_id


def _apply_subscription_feedback(subscription_rows: list[dict], transaction_rows: list[dict]):
    feedback_by_sub_id: dict[int, list[tuple[float, float]]] = {}
    for txn in transaction_rows:
        subscription_id = txn.get("subscription_id")
        if subscription_id is None:
            continue
        fvs = txn.get("feedback_value_score")
        fc = txn.get("feedback_confidence")
        if fvs is not None or fc is not None:
            feedback_by_sub_id.setdefault(subscription_id, []).append((fvs or 0.5, fc or 0.5))

    for sub in subscription_rows:
        pairs = feedback_by_sub_id.get(sub["id"], [])
        if not pairs:
            continue
        sub["feedback_value_score"] = round(max(0, min(1, sum(p[0] for p in pairs) / len(pairs))), 4)
        sub["feedback_confidence"] = round(max(0, min(1, sum(p[1] for p in pairs) / len(pairs))), 4)


def _build_subscription_valuation_rows(user_id: int, valuation_dicts: list[dict], subscription_rows_by_merchant: dict, model_version_id: int, now: datetime, start_id: int):
    rows = []
    next_id = start_id
    for value in valuation_dicts:
        merchant_name = value.get("_sub_ref", {}).get("merchant_info", {}).get("name", "")
        subscription_row = subscription_rows_by_merchant.get(merchant_name)
        if not subscription_row:
            continue

        rows.append(
            {
                "id": next_id,
                "user_id": user_id,
                "subscription_id": subscription_row["id"],
                "model_version_id": model_version_id,
                "context": value.get("context", "subscription_renewal"),
                "period_start": value["period_start"],
                "period_end": value["period_end"],
                "total_cost": value["total_cost"],
                "estimated_value": value["estimated_value"],
                "net_value": value["net_value"],
                "personal_value_score": value.get("personal_value_score"),
                "recommendation": value.get("recommendation", ""),
                "confidence": value.get("confidence", 0.7),
                "evidence_json": value.get("evidence_json", {}),
                "explanation_json": value.get("explanation_json", {}),
                "created_at": now,
            }
        )
        next_id += 1
    return rows, next_id


def _build_item_valuation_rows(user_id: int, valuation_dicts: list[dict], model_version_id: int, now: datetime, start_id: int):
    rows = []
    next_id = start_id
    for value in valuation_dicts:
        rows.append(
            {
                "id": next_id,
                "user_id": user_id,
                "item_name": value["item_name"],
                "item_category": value.get("item_category", ""),
                "model_version_id": model_version_id,
                "context": "one_off_purchase",
                "observed_price": value.get("observed_price"),
                "estimated_fair_price": value.get("estimated_fair_price"),
                "personal_value_score": value["personal_value_score"],
                "recommendation": value.get("recommendation", "wait"),
                "confidence": value.get("confidence", 0.7),
                "evidence_json": value.get("evidence_json", {}),
                "reasoning_json": value.get("reasoning_json", {}),
                "created_at": now,
            }
        )
        next_id += 1
    return rows, next_id


def _build_conversation_rows(user_id: int, conversation_dicts: list[dict], subscription_rows: list[dict], item_valuation_rows: list[dict], now: datetime, conversation_start_id: int, message_start_id: int):
    conversation_rows = []
    message_rows = []
    next_conversation_id = conversation_start_id
    next_message_id = message_start_id

    for conversation in conversation_dicts:
        linked_sub_idx = conversation.get("linked_sub_idx")
        linked_item_idx = conversation.get("linked_item_idx")
        linked_subscription = subscription_rows[linked_sub_idx]["id"] if linked_sub_idx is not None and linked_sub_idx < len(subscription_rows) else None
        linked_item = item_valuation_rows[linked_item_idx]["id"] if linked_item_idx is not None and linked_item_idx < len(item_valuation_rows) else None
        created_at = conversation.get("created_at") or now
        messages = conversation.get("messages", [])
        updated_at = messages[-1].get("created_at") if messages else created_at

        conversation_rows.append(
            {
                "id": next_conversation_id,
                "user_id": user_id,
                "title": conversation.get("title"),
                "context_type": conversation.get("context_type", "general"),
                "linked_subscription_id": linked_subscription,
                "linked_item_valuation_id": linked_item,
                "created_at": created_at,
                "updated_at": updated_at or created_at,
            }
        )

        for message in messages:
            message_rows.append(
                {
                    "id": next_message_id,
                    "conversation_id": next_conversation_id,
                    "role": message.get("role", "user"),
                    "content": message.get("content", ""),
                    "metadata_json": message.get("metadata_json", {}),
                    "created_at": message.get("created_at") or created_at,
                }
            )
            next_message_id += 1

        next_conversation_id += 1

    return conversation_rows, message_rows, next_conversation_id, next_message_id


def _build_fact_rows(user_id: int, generated_facts: list[dict], prefix: str, now: datetime, start_id: int):
    rows = []
    next_id = start_id
    all_facts = [
        {
            "fact_key": "seed_tag",
            "fact_value_json": {"seed": True, "prefix": prefix},
            "source": "seed",
            "confidence": Decimal("1.00"),
        }
    ]
    all_facts.extend(generated_facts)

    for fact in all_facts:
        rows.append(
            {
                "id": next_id,
                "user_id": user_id,
                "fact_key": fact["fact_key"],
                "fact_value_json": fact.get("fact_value_json", {}),
                "source": fact.get("source", "inferred"),
                "confidence": Decimal(str(fact.get("confidence", 0.80))),
                "updated_at": now,
            }
        )
        next_id += 1
    return rows, next_id


def _sync_subscription_rollups_from_generated_valuations(subscription_rows: list[dict], subscription_rows_by_merchant: dict, valuation_dicts: list[dict]):
    latest_by_subscription_id = {}
    for valuation in valuation_dicts:
        merchant_name = valuation.get("_sub_ref", {}).get("merchant_info", {}).get("name", "")
        subscription = subscription_rows_by_merchant.get(merchant_name)
        if subscription is None:
            continue
        subscription_id = subscription["id"]
        current = latest_by_subscription_id.get(subscription_id)
        valuation_key = (
            valuation["period_end"],
            valuation.get("subscription_utilization"),
            valuation.get("subscription_cost_benefit"),
        )
        current_key = None
        if current is not None:
            current_key = (
                current["period_end"],
                current.get("subscription_utilization"),
                current.get("subscription_cost_benefit"),
            )
        if current is None or valuation_key >= current_key:
            latest_by_subscription_id[subscription_id] = valuation

    for subscription in subscription_rows:
        latest = latest_by_subscription_id.get(subscription["id"])
        if latest is None:
            continue
        subscription["subscription_utilization"] = latest.get("subscription_utilization")
        subscription["subscription_cost_benefit"] = latest.get("subscription_cost_benefit")


def _is_behavioral_spend_row(txn: dict) -> bool:
    if txn.get("direction") != "spend":
        return False
    if txn.get("subscription_id") is not None:
        return False
    return txn.get("category") != "bills"


def _compute_raw_inferred_row(user_id: int, transaction_rows: list[dict], subscription_rows: list[dict], merchant_rows_by_id: dict, state: UserState, now: datetime):
    spend_txns = [txn for txn in transaction_rows if txn["direction"] == "spend"]
    behavioral_spend = [txn for txn in spend_txns if _is_behavioral_spend_row(txn)]
    if not behavioral_spend:
        return {
            "user_id": user_id,
            "window_days": 90,
            "avg_purchase_price": None,
            "purchase_price_variance": None,
            "category_distribution_json": {},
            "percent_impulsive_purchases": None,
            "regret_frequency": None,
            "brand_repetition_rate": None,
            "late_night_purchase_frequency": None,
            "avg_decision_time_minutes": None,
            "active_subscriptions_count": 0,
            "total_subscription_cost": Decimal("0.00"),
            "percent_income_spent_on_subscriptions": 0.0,
            "subscription_usage_frequency_json": {},
            "cancel_reactivation_frequency": None,
            "actual_monthly_spending": Decimal("0.00"),
            "computed_at": now,
        }

    amounts = [float(txn["amount"]) for txn in behavioral_spend]
    avg_price = sum(amounts) / len(amounts)
    variance = sum((amount - avg_price) ** 2 for amount in amounts) / max(1, len(amounts) - 1)

    category_counts: dict[str, int] = {}
    for txn in behavioral_spend:
        category = txn["category"]
        category_counts[category] = category_counts.get(category, 0) + 1
    category_distribution = {key: round(value / len(behavioral_spend), 3) for key, value in category_counts.items()}

    impulse_scores = [txn["impulse_score"] for txn in behavioral_spend if txn.get("impulse_score") is not None]
    regret_events = []
    for txn in behavioral_spend:
        score_event = bool(txn.get("regret_score") is not None and float(txn["regret_score"]) >= 0.25)
        rating_event = bool(txn.get("regret_rating") is not None and int(txn["regret_rating"]) >= 5)
        if txn.get("regret_score") is not None or txn.get("regret_rating") is not None:
            regret_events.append(score_event or rating_event)
    percent_impulsive = (
        sum(1 for score in impulse_scores if score > 0.5) / max(1, len(impulse_scores))
        if impulse_scores else None
    )
    regret_frequency = (
        sum(1 for flag in regret_events if flag) / max(1, len(regret_events))
        if regret_events else None
    )

    late_night_count = sum(1 for txn in behavioral_spend if txn["occurred_at"].hour >= 22 or txn["occurred_at"].hour < 5)
    late_night_frequency = late_night_count / len(behavioral_spend)

    merchant_counts: dict[str, int] = {}
    for txn in behavioral_spend:
        merchant_id = txn.get("merchant_id")
        if merchant_id is not None:
            key = str(merchant_id)
            merchant_counts[key] = merchant_counts.get(key, 0) + 1
    repeat_count = sum(1 for count in merchant_counts.values() if count > 1)
    brand_repetition = repeat_count / max(1, len(merchant_counts)) if merchant_counts else None

    active_subscriptions = [sub for sub in subscription_rows if sub["status"] == "active"]

    def _monthlyized_price(subscription_row: dict):
        price = float(subscription_row["price"])
        billing_cycle = subscription_row["billing_cycle"]
        if billing_cycle == "yearly":
            return price / 12.0
        if billing_cycle == "weekly":
            return price * 52.0 / 12.0
        return price

    total_subscription_cost = sum(_monthlyized_price(sub) for sub in active_subscriptions)
    monthly_income = max(1, float(state.monthly_income))

    decision_times = []
    for txn in behavioral_spend:
        considered_at = txn.get("considered_at")
        occurred_at = txn.get("occurred_at")
        if considered_at and occurred_at:
            diff_minutes = (occurred_at - considered_at).total_seconds() / 60.0
            if 0 < diff_minutes < 10000:
                decision_times.append(diff_minutes)
    avg_decision_time = sum(decision_times) / len(decision_times) if decision_times else None

    spend_dates = [txn["occurred_at"].date() for txn in spend_txns]
    window_days = max(1, (max(spend_dates) - min(spend_dates)).days + 1)
    months_in_window = max(1.0, window_days / 30.0)
    actual_monthly_spending = sum(float(txn["amount"]) for txn in spend_txns) / months_in_window

    subscription_usage = {}
    for sub in active_subscriptions:
        merchant = merchant_rows_by_id.get(sub["merchant_id"])
        category = merchant["category"] if merchant else "other"
        usage_frequency = sub.get("usage_frequency")
        if usage_frequency is None:
            continue
        if usage_frequency >= 5:
            subscription_usage[category] = "daily"
        elif usage_frequency >= 2:
            subscription_usage[category] = "weekly"
        else:
            subscription_usage[category] = "monthly"

    cancel_reactivation_count = sum(1 for sub in subscription_rows if sub.get("reactivation_count", 0) > 0)
    cancel_reactivation_frequency = cancel_reactivation_count / max(1, len(subscription_rows)) if subscription_rows else None

    return {
        "user_id": user_id,
        "window_days": min(365, window_days),
        "avg_purchase_price": Decimal(str(round(avg_price, 2))),
        "purchase_price_variance": Decimal(str(round(variance, 6))),
        "category_distribution_json": category_distribution,
        "percent_impulsive_purchases": round(percent_impulsive, 3) if percent_impulsive is not None else None,
        "regret_frequency": round(regret_frequency, 3) if regret_frequency is not None else None,
        "brand_repetition_rate": round(brand_repetition, 3) if brand_repetition is not None else None,
        "late_night_purchase_frequency": round(late_night_frequency, 3),
        "avg_decision_time_minutes": round(avg_decision_time, 2) if avg_decision_time is not None else None,
        "active_subscriptions_count": len(active_subscriptions),
        "total_subscription_cost": Decimal(str(round(total_subscription_cost, 2))),
        "percent_income_spent_on_subscriptions": round(total_subscription_cost / monthly_income, 4) if monthly_income > 0 else None,
        "subscription_usage_frequency_json": subscription_usage,
        "cancel_reactivation_frequency": round(cancel_reactivation_frequency, 3) if cancel_reactivation_frequency is not None else None,
        "actual_monthly_spending": Decimal(str(round(actual_monthly_spending, 2))),
        "computed_at": now,
    }


def run_csv_export(
    num_users: int = 50,
    months: int = 18,
    prefix: str = "seed_",
    password: str = "password123",
    use_llm: bool = False,
    max_llm_reflections: int = 20,
    seed: int | None = None,
    output_dir: str | Path = "exports/seed_csv",
    include_summary_json: bool = False,
    log: Callable[[str], None] = print,
) -> dict:
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    rng = make_rng(seed)
    reset_conversation_dedup()
    now = timezone.now()
    end_date = now.date()
    start_date = end_date - timedelta(days=30 * months)

    merchant_agent = MerchantAgent(rng, use_llm)
    merchant_catalog = merchant_agent.run(UserState(), {"start_date": start_date, "end_date": end_date})["merchant_catalog"]

    merchant_rows = []
    merchant_rows_by_name = {}
    merchant_rows_by_id = {}
    for merchant_id, merchant in enumerate(merchant_catalog, start=1):
        row = {
            "id": merchant_id,
            "name": merchant["name"],
            "category": merchant.get("category", "other"),
            "website_domain": merchant.get("domain") or "",
            "subscription_eligibility": merchant.get("eligibility", "not_subscribable"),
            "created_at": now,
            "updated_at": now,
        }
        merchant_rows.append(row)
        merchant_rows_by_name[row["name"]] = row
        merchant_rows_by_id[row["id"]] = row

    model_version_rows = [
        {"id": 1, "name": "subscription_value", "version": "v1", "description": "Agent-based synthetic data model version.", "created_at": now},
        {"id": 2, "name": "item_value", "version": "v1", "description": "Agent-based synthetic data model version.", "created_at": now},
    ]

    dataset = {
        "users": [],
        "raw_explicit": [],
        "raw_inferred": [],
        "computed": [],
        "preferences": [],
        "transactions": [],
        "subscriptions": [],
        "subscription_valuations": [],
        "item_valuations": [],
        "conversations": [],
        "messages": [],
        "facts": [],
    }
    summary_rows = []
    next_ids = {"preference": 1, "subscription": 1, "transaction": 1, "subscription_valuation": 1, "item_valuation": 1, "conversation": 1, "message": 1, "fact": 1}
    stats = {"users_created": 0, "transactions": 0, "subscriptions": 0, "sub_valuations": 0, "item_valuations": 0, "conversations": 0, "facts": 0, "anomalies": 0, "audit_repairs": 0}

    for index in range(num_users):
        user_seed = int(rng.integers(0, 2**31))
        user_rng = make_rng(user_seed)
        faker = _build_faker(user_seed)
        state = UserState()
        context = {"start_date": start_date, "end_date": end_date, "generated_at": now, "merchant_agent": MerchantAgent(user_rng, use_llm), "merchant_catalog": merchant_catalog, "max_llm_reflections": max_llm_reflections}

        persona_result = PersonaAgent(user_rng, use_llm).run(state, context)
        context.update(persona_result)
        for agent_cls in (IncomeAgent, SubscriptionAgent, ObligationAgent, SpendAgent, BehaviorAgent, AnomalyAgent):
            result = agent_cls(user_rng, use_llm).run(state, context)
            context.update(result)
        _apply_monthly_debt_transitions(state, context, start_date, end_date)
        valuation_result = ValuationAgent(user_rng, use_llm).run(state, context)
        context.update(valuation_result)
        conversation_result = ConversationAgent(user_rng, use_llm).run(state, context)
        context.update(conversation_result)
        audit_result = AuditAgent(user_rng, use_llm).run(state, context)

        user_id = index + 1
        username = f"{prefix}user_{index}"
        email = f"{username}@example.com"
        user_row = _build_user_row(user_id, username, email, faker, password, now)
        dataset["users"].append(user_row)
        raw_explicit_row = {"user_id": user_id, "display_name": f"{user_row['first_name']} {user_row['last_name']}", **persona_result["raw_explicit_data"], "created_at": now, "updated_at": now}
        dataset["raw_explicit"].append(raw_explicit_row)
        computed_row = {"user_id": user_id, **persona_result["computed_data"], "updated_at": now}
        dataset["computed"].append(computed_row)

        preference_rows, next_ids["preference"] = _build_preferences_rows(user_id, state, user_rng, now, next_ids["preference"])
        dataset["preferences"].extend(preference_rows)

        subscription_rows, next_ids["subscription"] = _build_subscriptions_rows(user_id, context.get("subscriptions", []), merchant_rows_by_name, now, next_ids["subscription"])
        dataset["subscriptions"].extend(subscription_rows)
        subscription_rows_by_merchant = {merchant_rows_by_id[row["merchant_id"]]["name"]: row for row in subscription_rows}

        all_transactions = []
        for key in ("income_transactions", "subscription_transactions", "obligation_transactions", "spend_transactions", "anomaly_transactions"):
            all_transactions.extend(context.get(key, []))
        transaction_rows, next_ids["transaction"] = _build_transactions_rows(user_id, all_transactions, merchant_rows_by_name, subscription_rows_by_merchant, now, next_ids["transaction"])
        _apply_subscription_feedback(subscription_rows, transaction_rows)
        dataset["transactions"].extend(transaction_rows)

        subscription_valuation_rows, next_ids["subscription_valuation"] = _build_subscription_valuation_rows(user_id, valuation_result.get("subscription_valuations", []), subscription_rows_by_merchant, 1, now, next_ids["subscription_valuation"])
        dataset["subscription_valuations"].extend(subscription_valuation_rows)
        _sync_subscription_rollups_from_generated_valuations(
            subscription_rows,
            subscription_rows_by_merchant,
            valuation_result.get("subscription_valuations", []),
        )
        item_valuation_rows, next_ids["item_valuation"] = _build_item_valuation_rows(user_id, valuation_result.get("item_valuations", []), 2, now, next_ids["item_valuation"])
        dataset["item_valuations"].extend(item_valuation_rows)
        conversation_rows, message_rows, next_ids["conversation"], next_ids["message"] = _build_conversation_rows(user_id, conversation_result.get("conversations", []), subscription_rows, item_valuation_rows, now, next_ids["conversation"], next_ids["message"])
        dataset["conversations"].extend(conversation_rows)
        dataset["messages"].extend(message_rows)

        fact_rows, next_ids["fact"] = _build_fact_rows(user_id, context.get("behavior_facts", []) + conversation_result.get("conversation_facts", []), prefix, now, next_ids["fact"])
        dataset["facts"].extend(fact_rows)
        raw_inferred_row = _compute_raw_inferred_row(user_id, transaction_rows, subscription_rows, merchant_rows_by_id, state, now)
        dataset["raw_inferred"].append(raw_inferred_row)
        state.apply_trait_drift(user_rng)

        stats["users_created"] += 1
        stats["transactions"] += len(transaction_rows)
        stats["subscriptions"] += len(subscription_rows)
        stats["sub_valuations"] += len(subscription_valuation_rows)
        stats["item_valuations"] += len(item_valuation_rows)
        stats["conversations"] += len(conversation_rows)
        stats["facts"] += len(fact_rows)
        stats["anomalies"] += len(context.get("anomaly_transactions", []))
        stats["audit_repairs"] += len(audit_result.get("audit_repairs", []))

        if include_summary_json:
            merchant_name_by_id = {row["id"]: row["name"] for row in merchant_rows}
            messages_by_conversation_id = {}
            for row in message_rows:
                messages_by_conversation_id.setdefault(row["conversation_id"], []).append(row)
            summary_rows.append(
                {
                    "user": user_row,
                    "raw_explicit": raw_explicit_row,
                    "raw_inferred": raw_inferred_row,
                    "computed": computed_row,
                    "preferences": preference_rows,
                    "transactions": [{**row, "merchant_name": merchant_name_by_id.get(row.get("merchant_id"))} for row in transaction_rows],
                    "subscriptions": [{**row, "merchant_name": merchant_name_by_id.get(row.get("merchant_id"))} for row in subscription_rows],
                    "subscription_valuations": subscription_valuation_rows,
                    "item_valuations": item_valuation_rows,
                    "conversations": [{**row, "messages": messages_by_conversation_id.get(row["id"], [])} for row in conversation_rows],
                    "facts": fact_rows,
                }
            )

        if (index + 1) % 10 == 0 or index == num_users - 1:
            log(f"  Generated CSV rows for user {index + 1}/{num_users}")

    _write_csv(output_path, User, dataset["users"], "users_user.csv")
    _write_csv(output_path, UserRawExplicit, dataset["raw_explicit"], "users_userrawexplicit.csv")
    _write_csv(output_path, UserRawInferred, dataset["raw_inferred"], "users_userrawinferred.csv")
    _write_csv(output_path, UserComputed, dataset["computed"], "users_usercomputed.csv")
    _write_csv(output_path, UserPreference, dataset["preferences"], "users_userpreference.csv")
    _write_csv(output_path, Transaction, dataset["transactions"], "transactions_transaction.csv")
    _write_csv(output_path, Subscription, dataset["subscriptions"], "subscriptions_subscription.csv")
    _write_csv(output_path, Merchant, merchant_rows, "subscriptions_merchant.csv")
    _write_csv(output_path, SubscriptionValuation, dataset["subscription_valuations"], "valuations_subscriptionvaluation.csv")
    _write_csv(output_path, ItemValuation, dataset["item_valuations"], "valuations_itemvaluation.csv")
    _write_csv(output_path, ValuationModelVersion, model_version_rows, "valuations_valuationmodelversion.csv")
    _write_csv(output_path, Conversation, dataset["conversations"], "ai_conversation.csv")
    _write_csv(output_path, Message, dataset["messages"], "ai_message.csv")
    _write_csv(output_path, UserFact, dataset["facts"], "ai_userfact.csv")

    if include_summary_json:
        with (output_path / "summary_by_user.json").open("w", encoding="utf-8") as handle:
            json.dump(summary_rows, handle, indent=2, default=_json_default)

    metadata = {
        "generator": "datagen.csv_seed.run_csv_export",
        "generated_at": now.isoformat(),
        "num_users": num_users,
        "months": months,
        "prefix": prefix,
        "seed": seed,
        "use_llm": use_llm,
        "include_summary_json": include_summary_json,
        "counts": {
            "users": len(dataset["users"]),
            "raw_explicit": len(dataset["raw_explicit"]),
            "raw_inferred": len(dataset["raw_inferred"]),
            "computed": len(dataset["computed"]),
            "preferences": len(dataset["preferences"]),
            "transactions": len(dataset["transactions"]),
            "subscriptions": len(dataset["subscriptions"]),
            "subscription_valuations": len(dataset["subscription_valuations"]),
            "item_valuations": len(dataset["item_valuations"]),
            "conversations": len(dataset["conversations"]),
            "messages": len(dataset["messages"]),
            "facts": len(dataset["facts"]),
            "merchants": len(merchant_rows),
            "model_versions": len(model_version_rows),
        },
    }
    with (output_path / "export_metadata.json").open("w", encoding="utf-8") as handle:
        json.dump(metadata, handle, indent=2)

    return stats
