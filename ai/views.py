from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal
import json
import logging
from pathlib import Path
import re

from django.conf import settings
from django.shortcuts import get_object_or_404
from django.utils import timezone
import requests
from subscriptions.models import BillingCycle, Subscription
from transactions.models import Transaction, TransactionCategory, TransactionDirection
from transactions.serializers import TransactionSerializer
from valuations.models import ItemValuation
from rest_framework.authentication import SessionAuthentication
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.viewsets import ModelViewSet, ReadOnlyModelViewSet

from .models import (
    Conversation,
    ConversationContext,
    ConversationMemoryItem,
    ConversationMemoryKind,
    Message,
    MessageRole,
    UserFact,
)
from .serializers import ConversationSerializer, MessageSerializer, UserFactSerializer
from .openai_config import get_openai_api_key, get_openai_api_key_issue
from .intents import INTENT_UPDATE_SATISFACTION, classify_intent
from .purchase_advisor import LOCAL_TO_ADVISOR_CATEGORY, extract_requested_category
from .authentication import DebugHeaderAuthentication
from users.models import BudgetStyle, FinancialGoal

SAFETY_GUARDRAILS = {
    "disclaimer": (
        "Zapp provides general financial guidance only and does not offer medical, legal, or tax advice."
    ),
    "restricted_guarantees": [
        "medical_outcomes",
        "legal_outcomes",
        "tax_outcomes",
    ],
    "safe_bounds": {
        "confidence": {"min": 0.0, "max": 1.0},
        "max_missing_data_questions": 1,
        "advice_scope": "general_financial_guidance",
    },
}

logger = logging.getLogger(__name__)
DEFAULT_OPENAI_MODEL = "gpt-4.1-mini"
OPENAI_AUTH_ERROR = "auth_error"
OPENAI_MALFORMED_KEY_ERROR = "malformed_api_key"
MONTHLY_SUBSCRIPTION_MULTIPLIERS = {
    BillingCycle.WEEKLY: Decimal("4.3333333333"),
    BillingCycle.MONTHLY: Decimal("1"),
    BillingCycle.YEARLY: Decimal("0.0833333333"),
}
RESPONSE_STYLE_BY_INTENT = {
    "ask": "direct_answer",
    "edit": "transformation",
    "recommend": "ranked_recommendations",
    "summarize": "summary",
}
FRONTEND_HINT_BY_RESPONSE_STYLE = {
    "direct_answer": "Render a normal assistant reply view.",
    "transformation": "Offer side-by-side/original-vs-rewrite UI.",
    "ranked_recommendations": "Show recommendation cards with rationale and a best next action.",
    "summary": "Use compact bullets and collapse long source text by default.",
    "navigation_options": "Display page navigation actions for transactions, subscriptions, and item valuations.",
}
MEMORY_RETRIEVAL_LIMIT = 4
MEMORY_CANDIDATE_LIMIT = 50
MEMORY_STOPWORDS = {
    "a",
    "about",
    "am",
    "an",
    "and",
    "are",
    "at",
    "be",
    "can",
    "could",
    "did",
    "do",
    "for",
    "from",
    "get",
    "had",
    "have",
    "help",
    "how",
    "i",
    "in",
    "is",
    "it",
    "let",
    "me",
    "my",
    "of",
    "on",
    "or",
    "please",
    "should",
    "tell",
    "that",
    "the",
    "this",
    "to",
    "we",
    "what",
    "when",
    "where",
    "which",
    "why",
    "with",
    "you",
}
MEMORY_TAG_RULES = {
    "budget": ("budget", "budgeting", "overspending", "save", "saving"),
    "spending": ("spend", "spending", "spent", "expense", "expenses"),
    "subscriptions": ("subscription", "subscriptions", "renew", "renewal", "cancel", "paused"),
    "income": ("income", "salary", "earn", "earned", "make", "made", "paycheck"),
    "groceries": ("grocery", "groceries"),
    "eating_out": ("restaurant", "restaurants", "food", "takeout", "delivery", "ubereats", "doordash"),
    "shopping": ("shopping", "shop", "amazon", "target"),
    "transport": ("transport", "uber", "lyft", "gas"),
}
INCOME_PATTERNS = [
    r"\bmy monthly income(?:\s+is|'s|=)?\s*\$?([\d,]+(?:\.\d{1,2})?)\b",
    r"\bi make\s+\$?([\d,]+(?:\.\d{1,2})?)\s+(?:a month|per month|monthly)\b",
    r"\bi earn\s+\$?([\d,]+(?:\.\d{1,2})?)\s+(?:a month|per month|monthly)\b",
]
GOAL_PATTERNS = {
    FinancialGoal.SAVE_MORE: (
        r"\b(save more|save money|saving more|cut spending|spend less)\b",
    ),
    FinancialGoal.REDUCE_DEBT: (
        r"\b(reduce debt|pay off debt|debt payoff|pay down debt)\b",
    ),
    FinancialGoal.BUILD_CREDIT: (
        r"\b(build credit|improve credit|raise my credit)\b",
    ),
    FinancialGoal.CONTROL_SUBS: (
        r"\b(control subscriptions|cut subscriptions|reduce subscriptions|subscription cleanup)\b",
    ),
    FinancialGoal.INVEST: (
        r"\b(invest more|start investing|grow investments)\b",
    ),
}
BUDGET_STYLE_PATTERNS = {
    BudgetStyle.STRICT: (
        r"\bstrict budget\b",
        r"\bvery strict\b",
    ),
    BudgetStyle.FLEXIBLE: (
        r"\bflexible budget\b",
        r"\bpretty flexible\b",
    ),
    BudgetStyle.OPTIMIZE_VALUE: (
        r"\boptimi[sz]e value\b",
        r"\bvalue focused\b",
        r"\bbest value\b",
    ),
}
SPEND_WINDOW_PATTERNS = [
    (r"\bin the last (\d+) days?\b", "days"),
    (r"\bover the last (\d+) days?\b", "days"),
    (r"\bpast (\d+) days?\b", "days"),
    (r"\blast (\d+) days?\b", "days"),
    (r"\bpast (\d+) weeks?\b", "weeks"),
    (r"\blast (\d+) weeks?\b", "weeks"),
]
SATISFACTION_TARGET_LOOKBACK_DAYS = 90
PENDING_ACTION_KEY = "pending_action"
PINNED_TRANSACTION_ID_KEY = "pinned_transaction_id"
LAST_SPEND_LOOKUP_KEY = "last_spend_lookup"
CHAT_ACTION_SELECT_SATISFACTION_TRANSACTION = "select_satisfaction_transaction"
CHAT_ACTION_CONFIRM_PENDING = "confirm_pending_action"
CHAT_ACTION_CANCEL_PENDING = "cancel_pending_action"
UI_ACTION_MESSAGE_KEY = "ui_action"
MONTH_NAME_TO_NUMBER = {
    "jan": 1,
    "january": 1,
    "feb": 2,
    "february": 2,
    "mar": 3,
    "march": 3,
    "apr": 4,
    "april": 4,
    "may": 5,
    "jun": 6,
    "june": 6,
    "jul": 7,
    "july": 7,
    "aug": 8,
    "august": 8,
    "sep": 9,
    "sept": 9,
    "september": 9,
    "oct": 10,
    "october": 10,
    "nov": 11,
    "november": 11,
    "dec": 12,
    "december": 12,
}
MONTH_NAME_PATTERN = (
    r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|"
    r"jul(?:y)?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
)
DATE_TOKEN_PATTERN = (
    rf"(?:\d{{4}}-\d{{1,2}}-\d{{1,2}}|\d{{1,2}}/\d{{1,2}}(?:/\d{{4}})?|"
    rf"{MONTH_NAME_PATTERN}\s+\d{{1,2}}(?:,\s*\d{{4}})?)"
)
EXACT_DATE_RANGE_PATTERNS = [
    re.compile(rf"\bfrom\s+({DATE_TOKEN_PATTERN})\s+to\s+({DATE_TOKEN_PATTERN})\b", re.IGNORECASE),
    re.compile(rf"\bbetween\s+({DATE_TOKEN_PATTERN})\s+and\s+({DATE_TOKEN_PATTERN})\b", re.IGNORECASE),
]
SPEND_CATEGORY_ALIASES = {
    TransactionCategory.GROCERIES: {"grocery", "groceries"},
    TransactionCategory.EATING_OUT: {"eating out", "restaurant", "restaurants", "food", "takeout", "coffee"},
    TransactionCategory.TRANSPORT: {"uber", "lyft", "gas", "transport"},
    TransactionCategory.SHOPPING: {"shopping", "amazon", "target"},
    TransactionCategory.BILLS: {"bill", "bills", "utilities", "internet", "rent"},
    TransactionCategory.ENTERTAINMENT: {"movie", "movies", "games", "concert", "entertainment"},
    TransactionCategory.HEALTH: {"doctor", "pharmacy", "health"},
    TransactionCategory.EDUCATION: {"course", "courses", "tuition", "education"},
    TransactionCategory.SUBSCRIPTIONS: {"subscription", "subscriptions", "streaming", "netflix", "spotify", "hulu"},
}
SPEND_CATEGORY_ALIAS_PATTERNS = {
    category: tuple(
        re.compile(rf"(?<!\w){re.escape(alias)}(?!\w)", re.IGNORECASE)
        for alias in sorted(aliases, key=len, reverse=True)
    )
    for category, aliases in SPEND_CATEGORY_ALIASES.items()
}
SPEND_CATEGORY_LABELS = {
    TransactionCategory.GROCERIES: "groceries",
    TransactionCategory.EATING_OUT: "eating out",
    TransactionCategory.TRANSPORT: "transport",
    TransactionCategory.SHOPPING: "shopping",
    TransactionCategory.BILLS: "bills",
    TransactionCategory.ENTERTAINMENT: "entertainment",
    TransactionCategory.HEALTH: "health",
    TransactionCategory.EDUCATION: "education",
    TransactionCategory.SUBSCRIPTIONS: "subscriptions",
}
SPEND_CATEGORY_SPENDING_LABELS = {
    TransactionCategory.GROCERIES: "grocery",
    TransactionCategory.EATING_OUT: "eating out",
    TransactionCategory.TRANSPORT: "transport",
    TransactionCategory.SHOPPING: "shopping",
    TransactionCategory.BILLS: "bill",
    TransactionCategory.ENTERTAINMENT: "entertainment",
    TransactionCategory.HEALTH: "health",
    TransactionCategory.EDUCATION: "education",
    TransactionCategory.SUBSCRIPTIONS: "subscription",
}
SATISFACTION_UPDATE_VERBS = ("set", "update", "change")
SATISFACTION_ACTION_PATTERN = re.compile(
    r"\b(?:set|update|change)\b.{0,80}\bsatisfaction(?:\s+(?:score|rating))?\b",
    re.IGNORECASE,
)
SATISFACTION_WORD_PATTERN = re.compile(r"\bsatisfaction(?:\s+(?:score|rating))?\b", re.IGNORECASE)
UNSUPPORTED_FEEDBACK_WORD_PATTERN = re.compile(
    r"\b(feedback(?:\s+(?:score|rating))?|score|rating)\b",
    re.IGNORECASE,
)
SATISFACTION_VALUE_PATTERNS = [
    re.compile(
        r"\bsatisfaction(?:\s+(?:score|rating))?\b.{0,80}?\b(?:to|as|=)\s*(\d{1,3})\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:set|update|change)\b.{0,80}?\bsatisfaction(?:\s+(?:score|rating))?\b.{0,40}?(\d{1,3})\b",
        re.IGNORECASE,
    ),
]
SATISFACTION_TARGET_STOPWORDS = {
    "a",
    "an",
    "as",
    "at",
    "by",
    "change",
    "for",
    "from",
    "my",
    "of",
    "on",
    "purchase",
    "satisfaction",
    "score",
    "set",
    "that",
    "the",
    "this",
    "to",
    "transaction",
    "update",
    "yesterday",
    "today",
}
SPEND_LOOKUP_CUE_PHRASES = (
    "how much",
    "what did",
    "show me",
    "tell me",
)
SPEND_REFINEMENT_PREFIXES = ("what about", "how about")
REFERENTIAL_TRANSACTION_PHRASES = (
    "that one",
    "that purchase",
    "that transaction",
    "same one",
    "same purchase",
    "it",
)
CONFIRM_MESSAGE_PATTERN = re.compile(r"^\s*confirm\b", re.IGNORECASE)
CANCEL_MESSAGE_PATTERN = re.compile(r"^\s*cancel\b", re.IGNORECASE)


def _format_recent_transaction(transaction):
    merchant_name = transaction.merchant.name if transaction.merchant else None
    return {
        "id": transaction.id,
        "amount": str(transaction.amount),
        "currency": transaction.currency,
        "direction": transaction.direction,
        "occurred_at": transaction.occurred_at.isoformat(),
        "category": transaction.category,
        "merchant": merchant_name,
        "description": transaction.description_raw,
    }


def _format_subscription(subscription):
    return {
        "id": subscription.id,
        "merchant": subscription.merchant.name,
        "status": subscription.status,
        "price": str(subscription.price),
        "currency": subscription.currency,
        "billing_cycle": subscription.billing_cycle,
        "renewal_date": subscription.renewal_date.isoformat() if subscription.renewal_date else None,
    }


def _format_user_fact(fact):
    return {
        "key": fact.fact_key,
        "value": fact.fact_value_json,
        "source": fact.source,
        "confidence": float(fact.confidence),
    }


def _format_money(amount: Decimal) -> str:
    return str(amount.quantize(Decimal("0.01")))


def _monthly_subscription_cost(subscription: Subscription) -> Decimal:
    multiplier = MONTHLY_SUBSCRIPTION_MULTIPLIERS.get(subscription.billing_cycle)
    if multiplier is None:
        return Decimal("0.00")
    return subscription.price * multiplier


def _get_response_style_for_intent(intent: str) -> str:
    return RESPONSE_STYLE_BY_INTENT.get(intent, RESPONSE_STYLE_BY_INTENT["ask"])


def _get_frontend_hint_for_response_style(response_style: str) -> str:
    return FRONTEND_HINT_BY_RESPONSE_STYLE.get(
        response_style,
        FRONTEND_HINT_BY_RESPONSE_STYLE["direct_answer"],
    )


def _build_local_direct_response(message: str) -> dict:
    return {
        "assistant_text": message,
        "response_style": "direct_answer",
        "frontend_hint": _get_frontend_hint_for_response_style("direct_answer"),
        "safety_guardrails": SAFETY_GUARDRAILS,
        "mode": "router",
    }


def _is_ui_action_message(message: Message) -> bool:
    metadata = getattr(message, "metadata_json", {}) or {}
    return bool(metadata.get(UI_ACTION_MESSAGE_KEY))


def _truncate_text(text: str, max_length: int = 180) -> str:
    compact = " ".join((text or "").split())
    if len(compact) <= max_length:
        return compact
    return f"{compact[: max_length - 3].rstrip()}..."


def _tokenize_memory_text(text: str) -> set[str]:
    return {
        token
        for token in re.findall(r"[a-z0-9]+", (text or "").lower())
        if len(token) > 2 and token not in MEMORY_STOPWORDS
    }


def _infer_memory_tags(text: str) -> list[str]:
    normalized = (text or "").lower()
    tags = set()
    tokens = _tokenize_memory_text(text)

    for tag, keywords in MEMORY_TAG_RULES.items():
        if any(keyword in normalized for keyword in keywords):
            tags.add(tag)

    for token in tokens:
        if token in {
            "budget",
            "spending",
            "subscriptions",
            "subscription",
            "income",
            "groceries",
            "shopping",
            "transport",
        }:
            tags.add(token.rstrip("s"))

    return sorted(tags)


def _parse_decimal_amount(raw_value: str) -> Decimal | None:
    normalized = (raw_value or "").replace(",", "").strip()
    if not normalized:
        return None
    try:
        return Decimal(normalized)
    except Exception:
        return None


def _extract_monthly_income_from_text(text: str) -> Decimal | None:
    normalized = (text or "").lower()
    for pattern in INCOME_PATTERNS:
        match = re.search(pattern, normalized)
        if match:
            amount = _parse_decimal_amount(match.group(1))
            if amount is not None:
                return amount.quantize(Decimal("0.01"))
    return None


def _extract_financial_goal_from_text(text: str) -> str | None:
    normalized = (text or "").lower()
    for goal, patterns in GOAL_PATTERNS.items():
        if any(re.search(pattern, normalized) for pattern in patterns):
            return goal
    return None


def _extract_budget_style_from_text(text: str) -> str | None:
    normalized = (text or "").lower()
    for budget_style, patterns in BUDGET_STYLE_PATTERNS.items():
        if any(re.search(pattern, normalized) for pattern in patterns):
            return budget_style
    return None


def _upsert_user_fact_memory(
    *,
    conversation: Conversation,
    source_message: Message,
    fact_key: str,
    fact_value_json: dict,
    summary_text: str,
    memory_kind: str,
    tags: list[str],
) -> None:
    UserFact.objects.update_or_create(
        user=conversation.user,
        fact_key=fact_key,
        defaults={
            "fact_value_json": fact_value_json,
            "source": "chat",
        },
    )
    ConversationMemoryItem.objects.update_or_create(
        user=conversation.user,
        dedupe_key=f"fact:{fact_key}",
        defaults={
            "conversation": conversation,
            "source_message": source_message,
            "memory_kind": memory_kind,
            "summary_text": summary_text,
            "detail_json": {
                "fact_key": fact_key,
                "fact_value": fact_value_json,
            },
            "tags_json": tags,
            "importance": 5,
        },
    )


def _remember_turn_topic(
    *,
    conversation: Conversation,
    source_message: Message,
    intent: str,
    tags: list[str],
) -> None:
    if not source_message.content:
        return

    memory_kind = ConversationMemoryKind.DECISION if intent == "recommend" else ConversationMemoryKind.TOPIC
    prefix = "User asked for advice" if intent == "recommend" else "User asked"
    summary_text = f"{prefix}: {_truncate_text(source_message.content)}"
    ConversationMemoryItem.objects.update_or_create(
        user=conversation.user,
        dedupe_key=f"message:{source_message.id}",
        defaults={
            "conversation": conversation,
            "source_message": source_message,
            "memory_kind": memory_kind,
            "summary_text": summary_text,
            "detail_json": {
                "intent": intent,
            },
            "tags_json": tags,
            "importance": 3 if intent == "recommend" else 2,
        },
    )


def _persist_turn_memory(
    *,
    conversation: Conversation,
    user_message: Message,
    intent_detection: dict,
) -> None:
    if _is_ui_action_message(user_message):
        return

    intent = intent_detection["intent"]
    if intent in {"smalltalk", "meta_help", "record_transaction", INTENT_UPDATE_SATISFACTION}:
        return

    tags = _infer_memory_tags(user_message.content)

    monthly_income = _extract_monthly_income_from_text(user_message.content)
    if monthly_income is not None:
        _upsert_user_fact_memory(
            conversation=conversation,
            source_message=user_message,
            fact_key="stated_monthly_income",
            fact_value_json={
                "value": float(monthly_income),
                "currency": "USD",
                "period": "monthly",
            },
            summary_text=f"User stated monthly income is ${_format_money(monthly_income)}.",
            memory_kind=ConversationMemoryKind.FACT,
            tags=sorted(set(tags + ["income"])),
        )

    financial_goal = _extract_financial_goal_from_text(user_message.content)
    if financial_goal:
        _upsert_user_fact_memory(
            conversation=conversation,
            source_message=user_message,
            fact_key="stated_financial_goal",
            fact_value_json={"value": financial_goal},
            summary_text=f"User stated financial goal: {financial_goal}.",
            memory_kind=ConversationMemoryKind.GOAL,
            tags=sorted(set(tags + ["goal"])),
        )

    budget_style = _extract_budget_style_from_text(user_message.content)
    if budget_style:
        _upsert_user_fact_memory(
            conversation=conversation,
            source_message=user_message,
            fact_key="stated_budget_style",
            fact_value_json={"value": budget_style},
            summary_text=f"User stated budget style: {budget_style}.",
            memory_kind=ConversationMemoryKind.PREFERENCE,
            tags=sorted(set(tags + ["budget"])),
        )

    if intent in {"ask", "recommend", "summarize", "edit"}:
        _remember_turn_topic(
            conversation=conversation,
            source_message=user_message,
            intent=intent,
            tags=tags,
        )


def build_retrieved_memories(
    conversation: Conversation,
    user_message: str,
    *,
    limit: int = MEMORY_RETRIEVAL_LIMIT,
) -> list[dict]:
    query_terms = _tokenize_memory_text(user_message)
    candidate_items = list(
        ConversationMemoryItem.objects.filter(user=conversation.user)
        .order_by("-updated_at")[:MEMORY_CANDIDATE_LIMIT]
    )
    scored_items = []
    now = timezone.now()

    for item in candidate_items:
        item_terms = _tokenize_memory_text(item.summary_text) | set(item.tags_json or [])
        overlap = len(query_terms & item_terms)
        age_days = max((now - item.updated_at).total_seconds() / 86400, 0)
        recency_score = max(0.0, 30.0 - age_days) / 30.0
        score = float(item.importance) * 2.0
        score += overlap * 4.0
        score += 1.5 if item.conversation_id == conversation.id else 0.0
        score += recency_score

        if overlap == 0:
            continue

        scored_items.append(
            (
                score,
                {
                    "memory_kind": item.memory_kind,
                    "summary_text": item.summary_text,
                    "tags": item.tags_json or [],
                    "importance": item.importance,
                    "updated_at": item.updated_at.isoformat(),
                },
            )
        )

    scored_items.sort(key=lambda entry: entry[0], reverse=True)
    return [item for _, item in scored_items[:limit]]


def _format_linked_context(conversation):
    linked_context = {}

    if conversation.linked_subscription_id:
        subscription = conversation.linked_subscription
        linked_context["subscription"] = {
            "id": subscription.id,
            "merchant": subscription.merchant.name,
            "status": subscription.status,
            "price": float(subscription.price),
            "currency": subscription.currency,
        }

    if conversation.linked_item_valuation_id:
        valuation = conversation.linked_item_valuation
        linked_context["item_valuation"] = {
            "id": valuation.id,
            "item_name": valuation.item_name,
            "item_category": valuation.item_category,
            "observed_price": float(valuation.observed_price) if valuation.observed_price is not None else None,
            "recommendation": valuation.recommendation,
            "confidence": valuation.confidence,
        }

    return linked_context




def _normalize_preference_value(preference):
    if not preference:
        return None

    value = preference.value_json
    if isinstance(value, dict) and set(value.keys()) == {"value"}:
        return value["value"]

    return value


def _get_purchase_advisor_logic(user):
    preference = user.preferences.filter(key="purchase_advisor_logic").first()
    value = _normalize_preference_value(preference)

    if value is True:
        return {
            "enabled": True,
            "lookback_days": 30,
            "overspending_ratio_threshold": 1.2,
        }

    if not isinstance(value, dict) or not value.get("enabled"):
        return None

    return {
        "enabled": True,
        "lookback_days": int(value.get("lookback_days", 30)),
        "overspending_ratio_threshold": float(value.get("overspending_ratio_threshold", 1.2)),
        "focus_categories": value.get("focus_categories") or [],
    }


def _build_purchase_advisor_profile_context(user):
    raw_explicit = getattr(user, "raw_explicit", None)
    computed = getattr(user, "computed", None)

    return {
        "life_stage": getattr(raw_explicit, "life_stage", "") or None,
        "financial_goal": getattr(raw_explicit, "financial_goal", "") or None,
        "budget_style": getattr(raw_explicit, "budget_style", "") or None,
        "spending_personality": getattr(computed, "spending_personality", "") or None,
        "budget_adherence_score": getattr(computed, "budget_adherence_score", None),
    }


def _normalize_transaction_category(category: str) -> str:
    return LOCAL_TO_ADVISOR_CATEGORY.get(category, "other")


def build_purchase_advisor_report(user, request_content: str):
    logic = _get_purchase_advisor_logic(user)
    if not logic:
        return None

    lookback_days = max(logic["lookback_days"], 1)
    overspending_ratio_threshold = max(logic["overspending_ratio_threshold"], 0.01)

    window_start = timezone.now() - timedelta(days=lookback_days)
    transactions = list(
        Transaction.objects.filter(
            user=user,
            direction=TransactionDirection.SPEND,
            occurred_at__gte=window_start,
        )
        .select_related("merchant")
        .order_by("-occurred_at")
    )
    if not transactions:
        return {
            "enabled": True,
            "status": "insufficient_data",
            "message": "Purchase advisor logic is enabled, but there is no spend history to analyze yet.",
            "lookback_days": lookback_days,
        }

    recent_transactions = transactions
    total_spend = sum((tx.amount for tx in recent_transactions), Decimal("0.00"))
    if total_spend <= 0:
        return {
            "enabled": True,
            "status": "insufficient_data",
            "message": "Purchase advisor logic is enabled, but there is no positive spend to compare yet.",
            "lookback_days": lookback_days,
        }

    category_totals = defaultdict(lambda: Decimal("0.00"))
    for tx in recent_transactions:
        advisor_category = _normalize_transaction_category(tx.category)
        category_totals[advisor_category] += tx.amount

    baseline_category_count = max(len(category_totals), 1)
    baseline_share = Decimal("1") / Decimal(str(baseline_category_count))
    requested_category = extract_requested_category(request_content)
    focus_categories = set(logic.get("focus_categories") or [])

    overspending_categories = []
    for category, amount in sorted(category_totals.items(), key=lambda item: item[1], reverse=True):
        if focus_categories and category not in focus_categories:
            continue
        share = amount / total_spend
        overspend_ratio = float(share / baseline_share)
        if overspend_ratio >= overspending_ratio_threshold:
            overspending_categories.append({
                "category": category,
                "total_spend": str(amount.quantize(Decimal("0.01"))),
                "share_of_spend": round(float(share), 4),
                "overspend_ratio": round(overspend_ratio, 2),
                "matches_request": category == requested_category,
            })

    targeted_report = None
    if requested_category and requested_category in category_totals:
        amount = category_totals[requested_category]
        share = amount / total_spend
        targeted_report = {
            "category": requested_category,
            "total_spend": str(amount.quantize(Decimal("0.01"))),
            "share_of_spend": round(float(share), 4),
            "overspend_ratio": round(float(share / baseline_share), 2),
            "is_overspending": any(item["category"] == requested_category for item in overspending_categories),
        }

    if requested_category and not targeted_report:
        targeted_report = {
            "category": requested_category,
            "total_spend": "0.00",
            "share_of_spend": 0.0,
            "overspend_ratio": 0.0,
            "is_overspending": False,
        }

    return {
        "enabled": True,
        "status": "ready",
        "lookback_days": lookback_days,
        "overspending_ratio_threshold": overspending_ratio_threshold,
        "requested_category": requested_category,
        "profile_context": _build_purchase_advisor_profile_context(user),
        "targeted_report": targeted_report,
        "overspending_categories": overspending_categories,
        "summary": {
            "total_spend": str(total_spend.quantize(Decimal("0.01"))),
            "category_count": len(category_totals),
            "top_category": overspending_categories[0]["category"] if overspending_categories else None,
        },
    }


def extract_requested_spend_window_days(request_content: str) -> int | None:
    normalized = (request_content or "").lower()

    for pattern, unit in SPEND_WINDOW_PATTERNS:
        match = re.search(pattern, normalized)
        if not match:
            continue
        quantity = max(int(match.group(1)), 1)
        if unit == "weeks":
            quantity *= 7
        return min(quantity, 365)

    if re.search(r"\blast week\b|\bpast week\b", normalized):
        return 7
    if re.search(r"\blast month\b|\bpast month\b", normalized):
        return 30

    return None


def _normalize_user_text(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def _format_human_date(value: date) -> str:
    return value.strftime("%B %d, %Y").replace(" 0", " ")


def _format_human_date_range(start: date, end: date) -> str:
    if start.year == end.year:
        return f"{start.strftime('%B')} {start.day} to {end.strftime('%B')} {end.day}, {start.year}"
    return f"{_format_human_date(start)} to {_format_human_date(end)}"


def _resolve_past_date(month: int, day: int, *, year: int | None, reference_date: date) -> date | None:
    candidate_year = year if year is not None else reference_date.year
    try:
        candidate = date(candidate_year, month, day)
    except ValueError:
        return None

    if year is not None or candidate <= reference_date:
        return candidate

    try:
        return date(reference_date.year - 1, month, day)
    except ValueError:
        return None


def _parse_date_token(token: str, *, reference_date: date) -> date | None:
    cleaned = (token or "").strip()
    if not cleaned:
        return None

    iso_match = re.fullmatch(r"(\d{4})-(\d{1,2})-(\d{1,2})", cleaned)
    if iso_match:
        year, month, day = (int(value) for value in iso_match.groups())
        return _resolve_past_date(month, day, year=year, reference_date=reference_date)

    slash_match = re.fullmatch(r"(\d{1,2})/(\d{1,2})(?:/(\d{4}))?", cleaned)
    if slash_match:
        month, day, year = slash_match.groups()
        return _resolve_past_date(
            int(month),
            int(day),
            year=int(year) if year else None,
            reference_date=reference_date,
        )

    month_name_match = re.fullmatch(
        rf"({MONTH_NAME_PATTERN})\s+(\d{{1,2}})(?:,\s*(\d{{4}}))?",
        cleaned,
        re.IGNORECASE,
    )
    if month_name_match:
        month_name, day, year = month_name_match.groups()
        month_number = MONTH_NAME_TO_NUMBER.get(month_name.lower())
        if month_number is None:
            return None
        return _resolve_past_date(
            month_number,
            int(day),
            year=int(year) if year else None,
            reference_date=reference_date,
        )

    return None


def _extract_explicit_date_range(request_content: str, *, reference_date: date) -> tuple[date, date] | None:
    for pattern in EXACT_DATE_RANGE_PATTERNS:
        match = pattern.search(request_content or "")
        if not match:
            continue

        start = _parse_date_token(match.group(1), reference_date=reference_date)
        end = _parse_date_token(match.group(2), reference_date=reference_date)
        if start is None or end is None or end < start:
            return None
        return start, end

    return None


def _extract_explicit_day(request_content: str, *, reference_date: date) -> date | None:
    normalized = _normalize_user_text(request_content)
    if "today" in normalized:
        return reference_date
    if "yesterday" in normalized:
        return reference_date - timedelta(days=1)

    for match in re.finditer(DATE_TOKEN_PATTERN, request_content or "", re.IGNORECASE):
        parsed = _parse_date_token(match.group(0), reference_date=reference_date)
        if parsed is not None:
            return parsed

    return None


def _extract_spend_category(request_content: str) -> str | None:
    for category, patterns in SPEND_CATEGORY_ALIAS_PATTERNS.items():
        if any(pattern.search(request_content or "") for pattern in patterns):
            return category
    return None


def _is_spend_lookup_refinement_request(normalized: str, previous_lookup: dict | None) -> bool:
    if not previous_lookup:
        return False
    return normalized.startswith(SPEND_REFINEMENT_PREFIXES) or "that week" in normalized


def _has_spend_lookup_language(normalized: str) -> bool:
    if any(phrase in normalized for phrase in SPEND_LOOKUP_CUE_PHRASES):
        return True
    return any(word in normalized for word in ("spent", "spend", "spending"))


def _week_bounds_for_date(anchor: date) -> tuple[date, date]:
    start = anchor - timedelta(days=anchor.weekday())
    return start, start + timedelta(days=6)


def _serialize_spend_lookup(lookup: dict) -> dict:
    return {
        "mode": lookup["mode"],
        "date_from": lookup["date_from"].isoformat(),
        "date_to": lookup["date_to"].isoformat(),
        "category": lookup.get("category"),
        "window_label": lookup.get("window_label"),
    }


def _deserialize_spend_lookup(raw_lookup: dict | None) -> dict | None:
    if not isinstance(raw_lookup, dict):
        return None

    try:
        date_from = date.fromisoformat(raw_lookup["date_from"])
        date_to = date.fromisoformat(raw_lookup["date_to"])
    except (KeyError, TypeError, ValueError):
        return None

    mode = raw_lookup.get("mode")
    if mode not in {"exact_day", "range", "rolling_window"}:
        return None

    return {
        "mode": mode,
        "date_from": date_from,
        "date_to": date_to,
        "category": raw_lookup.get("category"),
        "window_label": raw_lookup.get("window_label"),
    }


def parse_spend_lookup_request(request_content: str, *, previous_lookup: dict | None = None) -> dict | None:
    normalized = _normalize_user_text(request_content)
    reference_date = timezone.localdate()
    prior_lookup = _deserialize_spend_lookup(previous_lookup)
    is_refinement = _is_spend_lookup_refinement_request(normalized, prior_lookup)
    has_lookup_language = _has_spend_lookup_language(normalized)

    if not has_lookup_language and not is_refinement:
        return None

    category = _extract_spend_category(request_content)
    explicit_range = _extract_explicit_date_range(request_content, reference_date=reference_date)
    rolling_window_days = extract_requested_spend_window_days(request_content)
    exact_day = None if explicit_range or rolling_window_days else _extract_explicit_day(
        request_content,
        reference_date=reference_date,
    )

    if explicit_range:
        lookup = {
            "mode": "range",
            "date_from": explicit_range[0],
            "date_to": explicit_range[1],
            "category": category,
            "window_label": None,
        }
    elif rolling_window_days:
        lookup = {
            "mode": "rolling_window",
            "date_from": reference_date - timedelta(days=rolling_window_days - 1),
            "date_to": reference_date,
            "category": category,
            "window_label": _describe_spend_window(request_content, rolling_window_days),
        }
    elif exact_day is not None:
        lookup = {
            "mode": "exact_day",
            "date_from": exact_day,
            "date_to": exact_day,
            "category": category,
            "window_label": None,
        }
    elif is_refinement and prior_lookup:
        if "that week" in normalized:
            date_from, date_to = _week_bounds_for_date(prior_lookup["date_from"])
            lookup = {
                "mode": "range",
                "date_from": date_from,
                "date_to": date_to,
                "category": category or prior_lookup.get("category"),
                "window_label": None,
            }
        elif category:
            lookup = {
                "mode": prior_lookup["mode"],
                "date_from": prior_lookup["date_from"],
                "date_to": prior_lookup["date_to"],
                "category": category,
                "window_label": prior_lookup.get("window_label"),
            }
        else:
            return None
    else:
        return None

    if is_refinement and prior_lookup and lookup.get("category") is None:
        lookup["category"] = prior_lookup.get("category")

    return lookup


def _build_spend_lookup_context(user, lookup: dict) -> dict:
    queryset = Transaction.objects.filter(
        user=user,
        direction=TransactionDirection.SPEND,
        occurred_at__date__gte=lookup["date_from"],
        occurred_at__date__lte=lookup["date_to"],
    )
    if lookup.get("category"):
        queryset = queryset.filter(category=lookup["category"])

    transactions = list(queryset)
    total = sum((transaction.amount for transaction in transactions), Decimal("0.00"))
    return {
        "lookup": lookup,
        "total": _format_money(total),
        "transaction_count": len(transactions),
    }


def _build_spend_lookup_scope_prefix(lookup: dict) -> str:
    if lookup["mode"] == "exact_day":
        return f"On {_format_human_date(lookup['date_from'])}"
    if lookup["mode"] == "range":
        return f"From {_format_human_date_range(lookup['date_from'], lookup['date_to'])}"
    return f"In {lookup.get('window_label') or 'the last 30 days'}"


def _build_spend_lookup_scope_phrase(lookup: dict) -> str:
    if lookup["mode"] == "exact_day":
        return f"on {_format_human_date(lookup['date_from'])}"
    if lookup["mode"] == "range":
        return f"from {_format_human_date_range(lookup['date_from'], lookup['date_to'])}"
    return f"in {lookup.get('window_label') or 'the last 30 days'}"


def _build_spend_lookup_response(spend_lookup_context: dict) -> dict:
    lookup = spend_lookup_context["lookup"]
    category = lookup.get("category")
    transaction_count = spend_lookup_context["transaction_count"]
    scope_prefix = _build_spend_lookup_scope_prefix(lookup)
    scope_phrase = _build_spend_lookup_scope_phrase(lookup)

    if transaction_count == 0:
        if category:
            category_label = SPEND_CATEGORY_SPENDING_LABELS.get(
                category,
                (category or "spending").replace("_", " "),
            )
            return _build_local_direct_response(
                f"I don't see any {category_label} spending {scope_phrase}."
            )
        return _build_local_direct_response(f"I don't see any spending {scope_phrase}.")

    transaction_count_text = _format_transaction_count(transaction_count)
    total = spend_lookup_context["total"]
    if category:
        category_label = SPEND_CATEGORY_LABELS.get(category, category.replace("_", " "))
        return _build_local_direct_response(
            f"{scope_prefix}, you spent ${total} on {category_label} across {transaction_count_text}."
        )
    return _build_local_direct_response(
        f"{scope_prefix}, you spent ${total} across {transaction_count_text}."
    )


def build_financial_context(
    user,
    *,
    transaction_limit=5,
    subscription_limit=5,
    requested_spend_window_days: int | None = None,
):
    now = timezone.now()
    last_30_days_start = now - timedelta(days=30)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    recent_transactions = list(
        Transaction.objects.filter(user=user)
        .select_related("merchant")
        .order_by("-occurred_at")[:transaction_limit]
    )
    all_active_subscriptions = list(
        Subscription.objects.filter(user=user, status="active")
        .select_related("merchant")
        .order_by("renewal_date", "id")
    )
    active_subscriptions = all_active_subscriptions[:subscription_limit]
    spend_transactions_last_30_days = list(
        Transaction.objects.filter(
            user=user,
            direction=TransactionDirection.SPEND,
            occurred_at__gte=last_30_days_start,
        ).order_by("-occurred_at")
    )
    spend_transactions_month_to_date = list(
        Transaction.objects.filter(
            user=user,
            direction=TransactionDirection.SPEND,
            occurred_at__gte=month_start,
        ).order_by("-occurred_at")
    )
    spend_transactions_requested_window = []
    if requested_spend_window_days:
        requested_window_start = now - timedelta(days=requested_spend_window_days)
        spend_transactions_requested_window = list(
            Transaction.objects.filter(
                user=user,
                direction=TransactionDirection.SPEND,
                occurred_at__gte=requested_window_start,
            ).order_by("-occurred_at")
        )

    spend_amount = sum(
        (
            transaction.amount
            for transaction in recent_transactions
            if transaction.direction == TransactionDirection.SPEND
        ),
        Decimal("0.00"),
    )
    spend_last_30_days = sum(
        (transaction.amount for transaction in spend_transactions_last_30_days),
        Decimal("0.00"),
    )
    spend_month_to_date = sum(
        (transaction.amount for transaction in spend_transactions_month_to_date),
        Decimal("0.00"),
    )
    spend_requested_window = sum(
        (transaction.amount for transaction in spend_transactions_requested_window),
        Decimal("0.00"),
    )
    active_subscription_monthly_commitment = sum(
        (_monthly_subscription_cost(subscription) for subscription in all_active_subscriptions),
        Decimal("0.00"),
    )
    category_totals_30d = defaultdict(lambda: Decimal("0.00"))
    for transaction in spend_transactions_last_30_days:
        category_totals_30d[transaction.category] += transaction.amount
    category_totals_requested_window = defaultdict(lambda: Decimal("0.00"))
    for transaction in spend_transactions_requested_window:
        category_totals_requested_window[transaction.category] += transaction.amount

    spending_by_category_30d = [
        {
            "category": category,
            "amount": _format_money(amount),
        }
        for category, amount in sorted(category_totals_30d.items(), key=lambda item: item[1], reverse=True)
    ]
    spending_by_category_requested_window = [
        {
            "category": category,
            "amount": _format_money(amount),
        }
        for category, amount in sorted(
            category_totals_requested_window.items(),
            key=lambda item: item[1],
            reverse=True,
        )
    ]

    return {
        "recent_transactions": [_format_recent_transaction(tx) for tx in recent_transactions],
        "active_subscriptions": [_format_subscription(sub) for sub in active_subscriptions],
        "spending_by_category_30d": spending_by_category_30d,
        "spending_by_category_requested_window": spending_by_category_requested_window,
        "summary": {
            "transaction_count": len(recent_transactions),
            "active_subscription_count": len(all_active_subscriptions),
            "recent_spend_total": _format_money(spend_amount),
            "spend_last_30_days": _format_money(spend_last_30_days),
            "spend_transaction_count_last_30_days": len(spend_transactions_last_30_days),
            "spend_month_to_date": _format_money(spend_month_to_date),
            "spend_transaction_count_month_to_date": len(spend_transactions_month_to_date),
            "requested_spend_window_days": requested_spend_window_days,
            "spend_in_requested_window": _format_money(spend_requested_window)
            if requested_spend_window_days
            else None,
            "spend_transaction_count_in_requested_window": len(spend_transactions_requested_window)
            if requested_spend_window_days
            else None,
            "active_subscription_monthly_commitment": _format_money(active_subscription_monthly_commitment),
        },
    }


def _get_mutable_session_state(conversation) -> dict:
    current_state = conversation.session_state_json or {}
    if isinstance(current_state, dict):
        return dict(current_state)
    return {}


def _merge_session_state(existing_state: dict | None, derived_state: dict) -> dict:
    merged = dict(existing_state or {})
    merged.update(derived_state)
    return merged


def _build_chat_action(label: str, action_payload: dict, *, reason: str | None = None) -> dict:
    action = {
        "label": label,
        "action_payload": action_payload,
    }
    if reason:
        action["reason"] = reason
    return action


def _clear_pending_action(session_state: dict) -> None:
    session_state.pop(PENDING_ACTION_KEY, None)


def _get_pending_action(session_state: dict) -> dict | None:
    pending_action = session_state.get(PENDING_ACTION_KEY)
    if isinstance(pending_action, dict):
        return pending_action
    return None


def _get_pinned_spend_transaction(session_state: dict, *, user) -> Transaction | None:
    transaction_id = session_state.get(PINNED_TRANSACTION_ID_KEY)
    if not transaction_id:
        return None
    return (
        Transaction.objects.filter(
            id=transaction_id,
            user=user,
            direction=TransactionDirection.SPEND,
        )
        .select_related("merchant")
        .first()
    )


def _build_pending_satisfaction_state(
    *,
    value: int,
    status: str,
    transaction_id: int | None = None,
    candidate_transaction_ids: list[int] | None = None,
) -> dict:
    return {
        "kind": INTENT_UPDATE_SATISFACTION,
        "transaction_id": transaction_id,
        "candidate_transaction_ids": candidate_transaction_ids or [],
        "value": value,
        "status": status,
    }


def _get_transaction_reference_name(transaction: Transaction) -> str:
    if transaction.merchant and transaction.merchant.name:
        return transaction.merchant.name
    if transaction.description_raw:
        return transaction.description_raw
    return "that purchase"


def _format_candidate_transaction_label(transaction: Transaction) -> str:
    local_date = timezone.localtime(transaction.occurred_at).date()
    return (
        f"{_get_transaction_reference_name(transaction)}"
        f" • {local_date.strftime('%b')} {local_date.day}"
        f" • ${_format_money(transaction.amount)}"
    )


def _format_transaction_confirmation_reference(transaction: Transaction) -> str:
    local_date = timezone.localtime(transaction.occurred_at).date()
    return (
        f"{_get_transaction_reference_name(transaction)} on {_format_human_date(local_date)} "
        f"for ${_format_money(transaction.amount)}"
    )


def _build_update_satisfaction_confirmation_response(transaction: Transaction, value: int) -> dict:
    return {
        "assistant_text": (
            f"Update satisfaction for {_format_transaction_confirmation_reference(transaction)} to {value}?"
        ),
        "response_style": "direct_answer",
        "frontend_hint": _get_frontend_hint_for_response_style("direct_answer"),
        "safety_guardrails": SAFETY_GUARDRAILS,
        "mode": "router",
        "action": INTENT_UPDATE_SATISFACTION,
        "status": "needs_confirmation",
        "quick_actions": [
            _build_chat_action(
                "Confirm",
                {"kind": CHAT_ACTION_CONFIRM_PENDING},
                reason="Apply the pending satisfaction update.",
            ),
            _build_chat_action(
                "Cancel",
                {"kind": CHAT_ACTION_CANCEL_PENDING},
                reason="Discard the pending satisfaction update.",
            ),
        ],
    }


def _build_update_satisfaction_candidate_response(transactions: list[Transaction], value: int) -> dict:
    quick_actions = [
        _build_chat_action(
            _format_candidate_transaction_label(transaction),
            {
                "kind": CHAT_ACTION_SELECT_SATISFACTION_TRANSACTION,
                "transaction_id": transaction.id,
            },
            reason=f"Pick this spend transaction and then confirm satisfaction {value}.",
        )
        for transaction in transactions
    ]
    quick_actions.append(
        _build_chat_action(
            "Cancel",
            {"kind": CHAT_ACTION_CANCEL_PENDING},
            reason="Discard the pending satisfaction update.",
        )
    )
    return {
        "assistant_text": f"I found a few matching spend transactions for satisfaction {value}. Pick the right one.",
        "response_style": "direct_answer",
        "frontend_hint": _get_frontend_hint_for_response_style("direct_answer"),
        "safety_guardrails": SAFETY_GUARDRAILS,
        "mode": "router",
        "action": INTENT_UPDATE_SATISFACTION,
        "status": "needs_target",
        "quick_actions": quick_actions,
    }


def _build_update_satisfaction_completed_response(transaction: Transaction, value: int) -> dict:
    return _build_local_direct_response(
        f"Updated satisfaction for {_format_transaction_confirmation_reference(transaction)} to {value}."
    )


def _build_update_satisfaction_cancelled_response() -> dict:
    return _build_local_direct_response("Cancelled the pending satisfaction update.")


def _has_update_verb(normalized: str) -> bool:
    return any(re.search(rf"\b{verb}\b", normalized) for verb in SATISFACTION_UPDATE_VERBS)


def _is_unsupported_feedback_update_request(normalized: str) -> bool:
    return (
        _has_update_verb(normalized)
        and "satisfaction" not in normalized
        and "feedback" in normalized
        and bool(UNSUPPORTED_FEEDBACK_WORD_PATTERN.search(normalized))
    )


def _extract_satisfaction_value(request_content: str) -> tuple[int | None, str | None]:
    for pattern in SATISFACTION_VALUE_PATTERNS:
        match = pattern.search(request_content or "")
        if not match:
            continue

        value = int(match.group(1))
        if 1 <= value <= 10:
            return value, None
        return None, "Satisfaction needs to be a whole number from 1 to 10."

    return None, None


def _extract_transaction_amount_clue(request_content: str) -> Decimal | None:
    money_match = re.search(r"\$([\d,]+(?:\.\d{1,2})?)", request_content or "")
    if not money_match:
        money_match = re.search(
            r"\bamount(?:\s+of)?\s+\$?([\d,]+(?:\.\d{1,2})?)\b",
            request_content or "",
            re.IGNORECASE,
        )
    if not money_match:
        return None
    return _parse_decimal_amount(money_match.group(1))


def _extract_transaction_target_tokens(request_content: str) -> list[str]:
    cleaned = _normalize_user_text(request_content)
    cleaned = re.sub(DATE_TOKEN_PATTERN, " ", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\$[\d,]+(?:\.\d{1,2})?", " ", cleaned)
    cleaned = re.sub(r"\b\d{1,3}\b", " ", cleaned)
    tokens = []
    for token in re.findall(r"[a-z0-9]+", cleaned):
        if len(token) <= 1 or token in SATISFACTION_TARGET_STOPWORDS:
            continue
        tokens.append(token)
    return list(dict.fromkeys(tokens))


def _score_transaction_target_match(transaction: Transaction, tokens: list[str]) -> int:
    merchant_name = transaction.merchant.name if transaction.merchant else ""
    search_text = _normalize_user_text(f"{merchant_name} {transaction.description_raw or ''}")
    return sum(1 for token in tokens if token in search_text)


def _filter_transactions_for_satisfaction_target(
    queryset,
    *,
    explicit_day: date | None,
    amount: Decimal | None,
    tokens: list[str],
) -> list[Transaction]:
    if explicit_day is not None:
        queryset = queryset.filter(occurred_at__date=explicit_day)
    else:
        queryset = queryset.filter(
            occurred_at__gte=timezone.now() - timedelta(days=SATISFACTION_TARGET_LOOKBACK_DAYS)
        )

    if amount is not None:
        queryset = queryset.filter(amount=amount)

    candidates = list(queryset.select_related("merchant").order_by("-occurred_at")[:50])
    if not tokens:
        return candidates

    scored_candidates = []
    for transaction in candidates:
        score = _score_transaction_target_match(transaction, tokens)
        if score > 0:
            scored_candidates.append((score, transaction))

    if not scored_candidates:
        return []

    best_score = max(score for score, _transaction in scored_candidates)
    return [transaction for score, transaction in scored_candidates if score == best_score]


def _resolve_satisfaction_target_transactions(
    conversation: Conversation,
    request_content: str,
    *,
    session_state: dict,
) -> tuple[list[Transaction], str]:
    explicit_day = _extract_explicit_day(request_content, reference_date=timezone.localdate())
    amount = _extract_transaction_amount_clue(request_content)
    tokens = _extract_transaction_target_tokens(request_content)
    has_explicit_clues = explicit_day is not None or amount is not None or bool(tokens)

    if not has_explicit_clues:
        pinned_transaction = _get_pinned_spend_transaction(session_state, user=conversation.user)
        if pinned_transaction is not None:
            return [pinned_transaction], "pinned"
        return [], "missing_target"

    spend_matches = _filter_transactions_for_satisfaction_target(
        Transaction.objects.filter(
            user=conversation.user,
            direction=TransactionDirection.SPEND,
        ),
        explicit_day=explicit_day,
        amount=amount,
        tokens=tokens,
    )
    if spend_matches:
        return spend_matches, "matched"

    non_spend_matches = _filter_transactions_for_satisfaction_target(
        Transaction.objects.filter(user=conversation.user).exclude(direction=TransactionDirection.SPEND),
        explicit_day=explicit_day,
        amount=amount,
        tokens=tokens,
    )
    if non_spend_matches:
        return [], "non_spend_only"

    return [], "no_match"


def _handle_update_satisfaction_request(
    *,
    conversation: Conversation,
    request_content: str,
    session_state: dict,
) -> dict:
    normalized = _normalize_user_text(request_content)
    if _is_unsupported_feedback_update_request(normalized):
        return _build_local_direct_response(
            "I can only update satisfaction in chat right now. Please say satisfaction and a score from 1 to 10."
        )

    value, value_error = _extract_satisfaction_value(request_content)
    if value_error:
        return _build_local_direct_response(value_error)
    if value is None:
        return _build_local_direct_response("Tell me the satisfaction score from 1 to 10.")

    matches, resolution_status = _resolve_satisfaction_target_transactions(
        conversation,
        request_content,
        session_state=session_state,
    )

    if resolution_status == "non_spend_only":
        return _build_local_direct_response(
            "I can only update satisfaction for spend transactions."
        )

    if len(matches) == 1:
        transaction = matches[0]
        session_state[PENDING_ACTION_KEY] = _build_pending_satisfaction_state(
            value=value,
            status="needs_confirmation",
            transaction_id=transaction.id,
        )
        session_state[PINNED_TRANSACTION_ID_KEY] = transaction.id
        return _build_update_satisfaction_confirmation_response(transaction, value)

    if 2 <= len(matches) <= 3:
        session_state[PENDING_ACTION_KEY] = _build_pending_satisfaction_state(
            value=value,
            status="needs_target",
            candidate_transaction_ids=[transaction.id for transaction in matches],
        )
        return _build_update_satisfaction_candidate_response(matches, value)

    session_state[PENDING_ACTION_KEY] = _build_pending_satisfaction_state(
        value=value,
        status="needs_target",
    )
    if resolution_status == "missing_target":
        return _build_local_direct_response(
            "Which spend transaction should I update? Add the merchant, date, or amount."
        )
    if len(matches) > 3:
        return _build_local_direct_response(
            "I found too many matching spend transactions. Add the date or amount."
        )
    return _build_local_direct_response(
        "I couldn't find that spend transaction. Add the merchant, date, or amount."
    )


def _handle_pending_satisfaction_action(
    *,
    conversation: Conversation,
    request_content: str,
    session_state: dict,
    action_payload: dict | None,
) -> dict | None:
    pending_action = _get_pending_action(session_state)
    if not pending_action or pending_action.get("kind") != INTENT_UPDATE_SATISFACTION:
        return None

    normalized = _normalize_user_text(request_content)
    action_kind = action_payload.get("kind") if isinstance(action_payload, dict) else None

    if action_kind == CHAT_ACTION_CANCEL_PENDING or CANCEL_MESSAGE_PATTERN.match(normalized):
        _clear_pending_action(session_state)
        return _build_update_satisfaction_cancelled_response()

    if action_kind == CHAT_ACTION_SELECT_SATISFACTION_TRANSACTION:
        selected_id = action_payload.get("transaction_id")
        candidate_ids = pending_action.get("candidate_transaction_ids") or []
        if selected_id not in candidate_ids:
            return _build_local_direct_response("Pick one of the transactions I listed.")

        transaction = (
            Transaction.objects.filter(
                id=selected_id,
                user=conversation.user,
                direction=TransactionDirection.SPEND,
            )
            .select_related("merchant")
            .first()
        )
        if transaction is None:
            _clear_pending_action(session_state)
            return _build_local_direct_response(
                "I couldn't find that transaction anymore. Try the update again."
            )

        pending_action["transaction_id"] = transaction.id
        pending_action["status"] = "needs_confirmation"
        session_state[PENDING_ACTION_KEY] = pending_action
        session_state[PINNED_TRANSACTION_ID_KEY] = transaction.id
        return _build_update_satisfaction_confirmation_response(
            transaction,
            int(pending_action["value"]),
        )

    if action_kind == CHAT_ACTION_CONFIRM_PENDING or CONFIRM_MESSAGE_PATTERN.match(normalized):
        transaction_id = pending_action.get("transaction_id")
        if not transaction_id:
            return _build_local_direct_response("Pick the transaction first, then confirm.")

        transaction = (
            Transaction.objects.filter(
                id=transaction_id,
                user=conversation.user,
                direction=TransactionDirection.SPEND,
            )
            .select_related("merchant")
            .first()
        )
        if transaction is None:
            _clear_pending_action(session_state)
            return _build_local_direct_response(
                "I couldn't find that spend transaction anymore. Try the update again."
            )

        serializer = TransactionSerializer(
            transaction,
            data={"satisfaction_rating": pending_action["value"]},
            partial=True,
        )
        if not serializer.is_valid():
            _clear_pending_action(session_state)
            return _build_local_direct_response("I couldn't apply that satisfaction update.")

        serializer.save()
        _clear_pending_action(session_state)
        session_state[PINNED_TRANSACTION_ID_KEY] = transaction.id
        return _build_update_satisfaction_completed_response(
            transaction,
            int(pending_action["value"]),
        )

    return None


SHORT_TERM_MEMORY_MESSAGE_LIMIT = 6
SUMMARY_TRIGGER_MESSAGE_COUNT = 8
SUMMARY_MAX_TURNS = 12


def _summarize_messages(messages):
    summary_lines = []
    for message in messages:
        if _is_ui_action_message(message):
            continue

        role = message.role.capitalize()
        content = " ".join(message.content.split())
        if not content:
            continue
        if len(content) > 140:
            content = f"{content[:137]}..."
        summary_lines.append(f"{role}: {content}")
        if len(summary_lines) >= SUMMARY_MAX_TURNS:
            break
    return "\n".join(summary_lines)


def _extract_session_state(conversation, recent_messages):
    open_loops = []
    last_user_message = None
    mentioned_entities = []

    for message in recent_messages:
        if _is_ui_action_message(message):
            continue

        if message.role == MessageRole.USER:
            last_user_message = message.content
            content_lower = message.content.lower()
            if "?" in message.content:
                open_loops.append(message.content.strip())
            for keyword in ("budget", "subscription", "transaction", "purchase"):
                if keyword in content_lower and keyword not in mentioned_entities:
                    mentioned_entities.append(keyword)

    active_goal = "general_guidance"
    if conversation.context_type == ConversationContext.BUDGETING:
        active_goal = "budget_guidance"
    elif conversation.context_type == ConversationContext.SUBSCRIPTION:
        active_goal = "subscription_support"
    elif conversation.context_type == ConversationContext.PRODUCT:
        active_goal = "product_support"

    if last_user_message:
        detected_intent = classify_intent(last_user_message)["intent"]
        if detected_intent == "record_transaction":
            active_goal = "transaction_logging"
        elif detected_intent == INTENT_UPDATE_SATISFACTION:
            active_goal = "transaction_feedback"
        elif detected_intent == "smalltalk":
            active_goal = "casual_chat"
        elif detected_intent == "meta_help":
            active_goal = "product_help"
        elif detected_intent == "summarize":
            active_goal = "conversation_summary"
        elif detected_intent == "recommend":
            active_goal = "recommendation_support"

    return {
        "active_goal": active_goal,
        "open_loops": open_loops[-3:],
        "mentioned_entities": mentioned_entities,
        "last_user_message": last_user_message,
        "message_count": conversation.messages.count(),
    }


def build_conversation_memory(conversation, *, message_limit=SHORT_TERM_MEMORY_MESSAGE_LIMIT, recent_messages=None):
    if recent_messages is None:
        recent_messages = list(conversation.messages.order_by("created_at", "id"))

    recent_messages = [
        message for message in recent_messages
        if not _is_ui_action_message(message)
    ][-message_limit:]
    return {
        "summary_text": conversation.summary_text,
        "session_state": conversation.session_state_json or {},
        "recent_messages": [
            {
                "id": message.id,
                "role": message.role,
                "content": message.content,
                "created_at": message.created_at.isoformat(),
            }
            for message in recent_messages
        ],
    }


def refresh_conversation_memory(conversation):
    all_messages = list(conversation.messages.order_by("created_at", "id"))
    update_fields = ["session_state_json", "updated_at"]
    recent_messages = [
        message for message in all_messages
        if not _is_ui_action_message(message)
    ][-SHORT_TERM_MEMORY_MESSAGE_LIMIT:]

    if len(all_messages) >= SUMMARY_TRIGGER_MESSAGE_COUNT:
        already_summarized_until = conversation.last_summarized_message_id or 0
        messages_to_summarize = [
            message
            for message in all_messages[:-SHORT_TERM_MEMORY_MESSAGE_LIMIT]
            if message.id > already_summarized_until
        ]
        if messages_to_summarize:
            summarized_chunk = _summarize_messages(messages_to_summarize)
            conversation.summary_text = "\n".join(
                part for part in (conversation.summary_text, summarized_chunk) if part
            )
            conversation.last_summarized_message_id = messages_to_summarize[-1].id
            update_fields.extend(["summary_text", "last_summarized_message_id"])

    derived_state = _extract_session_state(conversation, recent_messages)
    conversation.session_state_json = _merge_session_state(
        conversation.session_state_json,
        derived_state,
    )
    conversation.save(update_fields=update_fields)
    return build_conversation_memory(conversation, recent_messages=recent_messages)


def _build_record_transaction_response() -> dict:
    return {
        "assistant_text": (
            "I can route you to the right page to save this directly. "
            "Choose where you want to update your data:"
        ),
        "response_style": "navigation_options",
        "frontend_hint": "Render quick action buttons for transaction/subscription/item update flows.",
        "safety_guardrails": SAFETY_GUARDRAILS,
        "action": "navigate_to_data_entry",
        "status": "routing_options",
        "created_transaction_id": None,
        "quick_actions": [
            {"label": "Add Transaction", "route": "/transactions/new"},
            {"label": "Manage Subscriptions", "route": "/subscriptions"},
            {"label": "Add Subscription", "route": "/subscriptions/new"},
            {"label": "Add Item Valuation", "route": "/valuations/new"},
        ],
    }


def _build_smalltalk_response(intent_detection: dict) -> dict:
    variant = intent_detection.get("signals", {}).get("smalltalk_variant")
    if variant == "gratitude":
        message = "You're welcome. If you want, ask me about your spending, subscriptions, or budget."
    elif variant == "closing":
        message = "Talk soon."
    else:
        message = (
            "Hi. I can help with spending, subscriptions, and budgeting. "
            "Try asking something like 'How much did I spend in the last 30 days?'"
        )
    return _build_local_direct_response(message)


def _build_meta_help_response() -> dict:
    return _build_local_direct_response(
        "I can answer questions about your spending, subscriptions, and recent activity, "
        "or help with budget decisions. Try asking 'How much am I spending?', "
        "'What subscriptions are active?', or 'What should I cut first?'"
    )


def _format_category_label(category: str | None) -> str:
    return ((category or "other").replace("_", " ")).title()


def _format_transaction_count(count: int) -> str:
    return f"{count} transaction" if count == 1 else f"{count} transactions"


def _describe_spend_window(request_content: str, requested_spend_window_days: int | None) -> str:
    normalized = (request_content or "").lower()

    if re.search(r"\blast week\b|\bpast week\b", normalized):
        return "the last week"
    if re.search(r"\blast month\b|\bpast month\b", normalized):
        return "the last month"

    week_match = re.search(r"\b(?:past|last)\s+(\d+)\s+weeks?\b", normalized)
    if week_match:
        quantity = max(int(week_match.group(1)), 1)
        return f"the last {quantity} week" if quantity == 1 else f"the last {quantity} weeks"

    if requested_spend_window_days == 1:
        return "the last day"
    if requested_spend_window_days:
        return f"the last {requested_spend_window_days} days"
    return "the last 30 days"


def _build_spend_summary_response(request_content: str, financial_context: dict) -> dict:
    summary = financial_context.get("summary", {})
    requested_spend_window_days = summary.get("requested_spend_window_days")

    if requested_spend_window_days:
        spend_amount = summary.get("spend_in_requested_window") or "0.00"
        transaction_count = summary.get("spend_transaction_count_in_requested_window") or 0
        spend_label = _describe_spend_window(request_content, requested_spend_window_days)
        top_categories = financial_context.get("spending_by_category_requested_window", [])

        if transaction_count == 0:
            return _build_local_direct_response(
                f"Based on the transactions I can see, you haven't spent anything in {spend_label}."
            )

        message = (
            f"Based on the transactions I can see, you've spent ${spend_amount} in {spend_label} "
            f"across {_format_transaction_count(transaction_count)}."
        )
        if top_categories:
            top_category = top_categories[0]
            message += (
                f" Your biggest category in that window is "
                f"{_format_category_label(top_category['category'])} at ${top_category['amount']}."
            )
        return _build_local_direct_response(message)

    spend_last_30_days = summary.get("spend_last_30_days", "0.00")
    spend_transaction_count_last_30_days = summary.get("spend_transaction_count_last_30_days", 0)
    spend_month_to_date = summary.get("spend_month_to_date", "0.00")
    spend_transaction_count_month_to_date = summary.get("spend_transaction_count_month_to_date", 0)
    active_subscription_monthly_commitment = summary.get("active_subscription_monthly_commitment", "0.00")
    top_categories = financial_context.get("spending_by_category_30d", [])

    if spend_transaction_count_last_30_days == 0:
        return _build_local_direct_response(
            "Based on the transactions I can see, you haven't spent anything in the last 30 days yet."
        )

    message = (
        f"Based on the transactions I can see, you've spent ${spend_last_30_days} in the last 30 days "
        f"across {_format_transaction_count(spend_transaction_count_last_30_days)}. "
        f"Month to date, that's ${spend_month_to_date} across "
        f"{_format_transaction_count(spend_transaction_count_month_to_date)}."
    )
    if top_categories:
        top_category = top_categories[0]
        message += (
            f" Your biggest category right now is "
            f"{_format_category_label(top_category['category'])} at ${top_category['amount']}."
        )
    if Decimal(active_subscription_monthly_commitment) > 0:
        message += (
            f" Your active subscriptions add about ${active_subscription_monthly_commitment} per month."
        )
    return _build_local_direct_response(message)


def _load_system_prompt() -> str:
    prompt_path = Path(__file__).resolve().parent / "prompts" / "system_prompt_v1.txt"
    return prompt_path.read_text(encoding="utf-8").strip()


def _build_openai_payload(
    conversation,
    user_message: str,
    financial_context: dict,
    purchase_advisor_report: dict | None,
    conversation_memory: dict,
    recalled_memories: list[dict],
    intent: str,
):
    raw_explicit = getattr(conversation.user, "raw_explicit", None)

    subscriptions = []
    for subscription in financial_context.get("active_subscriptions", []):
        subscriptions.append(
            {
                "name": subscription["merchant"],
                "status": subscription["status"],
                "billing_cycle": subscription["billing_cycle"],
                "price": float(subscription["price"]),
                "currency": subscription["currency"],
                "renewal_date": subscription["renewal_date"],
                "usage_frequency": None,
            }
        )

    recent_transactions = []
    for transaction in financial_context.get("recent_transactions", []):
        recent_transactions.append(
            {
                "merchant": transaction["merchant"],
                "amount": float(transaction["amount"]),
                "currency": transaction["currency"],
                "direction": transaction["direction"],
                "occurred_at": transaction["occurred_at"],
                "category": transaction["category"],
                "description": transaction["description"],
            }
        )

    recent_messages = [
        {
            "role": message["role"],
            "content": message["content"],
        }
        for message in conversation_memory.get("recent_messages", [])
    ]

    return {
        "schema_version": "zapp_prompt_v1",
        "request": {
            "intent": intent,
            "response_style": _get_response_style_for_intent(intent),
        },
        "conversation": {
            "conversation_id": conversation.id,
            "context_type": conversation.context_type,
            "user_message": user_message,
            "linked_context": _format_linked_context(conversation),
            "memory": {
                "summary_text": conversation_memory.get("summary_text", ""),
                "session_state": conversation_memory.get("session_state", {}),
                "recent_messages": recent_messages,
                "recalled_memories": recalled_memories,
            },
        },
        "profile": {
            "monthly_income": float(raw_explicit.monthly_income) if raw_explicit and raw_explicit.monthly_income is not None else None,
            "monthly_fixed_expenses": float(raw_explicit.monthly_fixed_expenses)
            if raw_explicit and raw_explicit.monthly_fixed_expenses is not None
            else None,
            "financial_goal": raw_explicit.financial_goal if raw_explicit and raw_explicit.financial_goal else None,
            "risk_tolerance": raw_explicit.risk_tolerance if raw_explicit and raw_explicit.risk_tolerance else None,
            "budget_style": raw_explicit.budget_style if raw_explicit and raw_explicit.budget_style else None,
        },
        "spending": {
            "summary": {
                "recent_spend_total": float(financial_context["summary"]["recent_spend_total"]),
                "spend_last_30_days": float(financial_context["summary"]["spend_last_30_days"]),
                "spend_transaction_count_last_30_days": financial_context["summary"]["spend_transaction_count_last_30_days"],
                "spend_month_to_date": float(financial_context["summary"]["spend_month_to_date"]),
                "spend_transaction_count_month_to_date": financial_context["summary"]["spend_transaction_count_month_to_date"],
                "requested_spend_window_days": financial_context["summary"]["requested_spend_window_days"],
                "spend_in_requested_window": float(financial_context["summary"]["spend_in_requested_window"])
                if financial_context["summary"]["spend_in_requested_window"] is not None
                else None,
                "spend_transaction_count_in_requested_window": financial_context["summary"][
                    "spend_transaction_count_in_requested_window"
                ],
                "active_subscription_monthly_commitment": float(
                    financial_context["summary"]["active_subscription_monthly_commitment"]
                ),
            },
            "category_spend_30d": [
                {
                    "category": totals["category"],
                    "amount": float(totals["amount"]),
                }
                for totals in financial_context.get("spending_by_category_30d", [])
            ],
            "recent_transactions": recent_transactions,
        },
        "subscriptions": subscriptions,
        "user_facts": [
            _format_user_fact(fact)
            for fact in conversation.user.facts.order_by("fact_key")
        ],
        "purchase_advisor_report": purchase_advisor_report,
        "policy": {
            "must_not_fabricate_data": True,
            "ask_clarifying_question_when_missing_data": True,
            "max_follow_up_questions": 1,
            "disclaimer_required_for_specialized_guidance": True,
            "answer_based_on_available_context_when_possible": True,
            "state_context_scope_when_data_is_partial": True,
            "safe_next_step_bias": "reversible_actions_only",
        },
    }


def _format_openai_response_text(payload: dict) -> str:
    message = (payload.get("message") or "").strip()
    follow_up_question = (payload.get("follow_up_question") or "").strip()
    disclaimer = (payload.get("disclaimer") or "").strip()

    parts = [part for part in (message, follow_up_question, disclaimer) if part]
    if not parts:
        return "I don't have enough context yet to answer that clearly."
    return " ".join(parts)


def build_openai_assistant_response(
    *,
    conversation,
    user_message: str,
    financial_context: dict,
    purchase_advisor_report: dict | None,
    conversation_memory: dict,
    recalled_memories: list[dict],
    intent: str,
) -> dict:
    api_key = get_openai_api_key()
    if not api_key:
        raise ValueError("OpenAI API key is missing.")

    payload = _build_openai_payload(
        conversation,
        user_message,
        financial_context,
        purchase_advisor_report,
        conversation_memory,
        recalled_memories,
        intent,
    )
    response = requests.post(
        "https://api.openai.com/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json={
            "model": getattr(settings, "OPENAI_MODEL", DEFAULT_OPENAI_MODEL) or DEFAULT_OPENAI_MODEL,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": _load_system_prompt()},
                {"role": "user", "content": json.dumps(payload)},
            ],
        },
        timeout=30,
    )
    response.raise_for_status()
    body = response.json()
    content = body["choices"][0]["message"]["content"]
    parsed = json.loads(content)
    response_style = parsed.get("response_style")
    if response_style not in FRONTEND_HINT_BY_RESPONSE_STYLE:
        response_style = _get_response_style_for_intent(intent)

    return {
        "assistant_text": _format_openai_response_text(parsed),
        "response_style": response_style,
        "frontend_hint": _get_frontend_hint_for_response_style(response_style),
        "safety_guardrails": SAFETY_GUARDRAILS,
        "model_response_json": parsed,
    }


def _classify_openai_error(exc: Exception) -> str | None:
    if isinstance(exc, requests.HTTPError) and exc.response is not None and exc.response.status_code == 401:
        return OPENAI_AUTH_ERROR
    return None


def build_assistant_placeholder_response(
    intent: str,
    *,
    openai_configured: bool,
    openai_failed: bool = False,
    openai_error_code: str | None = None,
) -> dict:
    if openai_error_code == OPENAI_AUTH_ERROR:
        base_suffix = "OpenAI rejected the server API key, so I'm using the local fallback."
    elif openai_error_code == OPENAI_MALFORMED_KEY_ERROR:
        base_suffix = "OpenAI API key looks malformed in the server configuration, so I'm using the local fallback."
    elif openai_failed:
        base_suffix = "OpenAI response is temporarily unavailable, so I'm using the local fallback."
    elif openai_configured:
        base_suffix = "OpenAI call wiring is the next step."
    else:
        base_suffix = "OpenAI key is not configured yet."
    safety_suffix = (
        f" {SAFETY_GUARDRAILS['disclaimer']} Responses must stay within safe bounds and avoid guarantees."
    )

    intent_templates = {
        "record_transaction": {
            "message": (
                "✅ I understand this as a transaction logging request. "
                "I can route the user to the correct data-entry page instead of writing directly from chat."
            ),
            "response_style": "navigation_options",
            "frontend_hint": _get_frontend_hint_for_response_style("navigation_options"),
        },
        "ask": {
            "message": (
                "✅ I understand this as a question. "
                "I can answer directly and ask one follow-up only if critical details are missing."
            ),
            "response_style": _get_response_style_for_intent("ask"),
            "frontend_hint": _get_frontend_hint_for_response_style("direct_answer"),
        },
        "edit": {
            "message": (
                "✅ I understand this as an edit request. "
                "I should return a revised version of the user-provided text with minimal extra commentary."
            ),
            "response_style": _get_response_style_for_intent("edit"),
            "frontend_hint": _get_frontend_hint_for_response_style("transformation"),
        },
        "recommend": {
            "message": (
                "✅ I understand this as a recommendation request. "
                "I should provide ranked options, the reasoning behind each option, and a best next action."
            ),
            "response_style": _get_response_style_for_intent("recommend"),
            "frontend_hint": _get_frontend_hint_for_response_style("ranked_recommendations"),
        },
        "summarize": {
            "message": (
                "✅ I understand this as a summarization request. "
                "I should return concise key points and optional action items."
            ),
            "response_style": _get_response_style_for_intent("summarize"),
            "frontend_hint": _get_frontend_hint_for_response_style("summary"),
        },
    }

    template = intent_templates.get(intent, intent_templates["ask"])
    return {
        "assistant_text": f"{template['message']} ({base_suffix}){safety_suffix}",
        "response_style": template["response_style"],
        "frontend_hint": template["frontend_hint"],
        "safety_guardrails": SAFETY_GUARDRAILS,
    }


class AIViewSetMixin:
    authentication_classes = [SessionAuthentication, DebugHeaderAuthentication]
    permission_classes = [IsAuthenticated]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "ai"


class ConversationViewSet(AIViewSetMixin, ModelViewSet):
    serializer_class = ConversationSerializer
    http_method_names = ["get", "post", "delete", "head", "options"]

    def get_queryset(self):
        return Conversation.objects.filter(user=self.request.user).select_related(
            "linked_subscription__merchant",
            "linked_item_valuation",
        )

    def create(self, request, *args, **kwargs):
        context_type = request.data.get("context_type", ConversationContext.GENERAL)
        if context_type not in ConversationContext.values:
            return Response(
                {"detail": "Invalid context_type"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        title = request.data.get("title")
        linked_subscription_id = request.data.get("linked_subscription")
        linked_item_valuation_id = request.data.get("linked_item_valuation")

        linked_subscription = None
        if linked_subscription_id is not None:
            linked_subscription = get_object_or_404(
                Subscription.objects.select_related("merchant"),
                id=linked_subscription_id,
                user=request.user,
            )

        linked_item_valuation = None
        if linked_item_valuation_id is not None:
            linked_item_valuation = get_object_or_404(
                ItemValuation.objects.all(),
                id=linked_item_valuation_id,
                user=request.user,
            )

        convo = Conversation.objects.create(
            user=request.user,
            context_type=context_type,
            title=title,
            linked_subscription=linked_subscription,
            linked_item_valuation=linked_item_valuation,
        )

        return Response({"conversation_id": convo.id}, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["get", "post"], url_path="messages")
    def messages(self, request, pk=None):
        """
        GET  /api/ai/conversations/<id>/messages/
        POST /api/ai/conversations/<id>/messages/  body: { "content": "..." }
        """
        convo = get_object_or_404(self.get_queryset(), id=pk)

        if request.method.lower() == "get":
            qs = Message.objects.filter(conversation=convo).order_by("created_at")
            return Response(MessageSerializer(qs, many=True).data)

        # POST: create user msg + assistant reply
        action_payload = request.data.get("action_payload")
        if action_payload is not None and not isinstance(action_payload, dict):
            return Response(
                {"detail": "action_payload must be an object"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        content = request.data.get("content", "")
        content = (content or "").strip()
        if not content and isinstance(action_payload, dict):
            content = str(action_payload.get("label") or "").strip()
        if not content and action_payload is None:
            return Response({"detail": "content is required"}, status=status.HTTP_400_BAD_REQUEST)

        intent_detection = classify_intent(content)
        user_metadata = {"intent_detection": intent_detection}
        if action_payload is not None:
            user_metadata["action_payload"] = action_payload
            user_metadata[UI_ACTION_MESSAGE_KEY] = True

        user_msg = Message.objects.create(
            conversation=convo,
            role=MessageRole.USER,
            content=content,
            metadata_json=user_metadata,
        )

        openai_key_issue = get_openai_api_key_issue()
        openai_configured = openai_key_issue is None and bool(get_openai_api_key())
        intent = intent_detection["intent"]
        openai_error_code = OPENAI_MALFORMED_KEY_ERROR if openai_key_issue and openai_key_issue != "missing" else None
        session_state = _get_mutable_session_state(convo)
        financial_context = {}
        purchase_advisor_report = None
        prompt_conversation_memory = None
        recalled_memories = []
        spend_lookup_request = None
        spend_summary_query = intent_detection.get("signals", {}).get("spend_summary_query", False)
        normalized_content = _normalize_user_text(content)
        pending_action_payload = _handle_pending_satisfaction_action(
            conversation=convo,
            request_content=content,
            session_state=session_state,
            action_payload=action_payload,
        )

        if pending_action_payload is not None:
            assistant_payload = pending_action_payload
        elif intent == INTENT_UPDATE_SATISFACTION or _is_unsupported_feedback_update_request(normalized_content):
            assistant_payload = _handle_update_satisfaction_request(
                conversation=convo,
                request_content=content,
                session_state=session_state,
            )
        else:
            previous_spend_lookup = session_state.get(LAST_SPEND_LOOKUP_KEY)
            if intent == "ask":
                spend_lookup_request = parse_spend_lookup_request(
                    content,
                    previous_lookup=previous_spend_lookup,
                )

            if spend_lookup_request is not None:
                session_state[LAST_SPEND_LOOKUP_KEY] = _serialize_spend_lookup(spend_lookup_request)
                assistant_payload = _build_spend_lookup_response(
                    _build_spend_lookup_context(request.user, spend_lookup_request)
                )
            elif intent == "record_transaction":
                assistant_payload = _build_record_transaction_response()
            elif intent == "smalltalk":
                assistant_payload = _build_smalltalk_response(intent_detection)
            elif intent == "meta_help":
                assistant_payload = _build_meta_help_response()
            else:
                requested_spend_window_days = extract_requested_spend_window_days(content)
                financial_context = build_financial_context(
                    request.user,
                    requested_spend_window_days=requested_spend_window_days,
                )
                if intent == "ask" and spend_summary_query:
                    assistant_payload = _build_spend_summary_response(content, financial_context)
                else:
                    purchase_advisor_report = build_purchase_advisor_report(request.user, content)
                    prompt_conversation_memory = refresh_conversation_memory(convo)
                    recalled_memories = build_retrieved_memories(convo, content)
                    if openai_configured:
                        try:
                            assistant_payload = build_openai_assistant_response(
                                conversation=convo,
                                user_message=content,
                                financial_context=financial_context,
                                purchase_advisor_report=purchase_advisor_report,
                                conversation_memory=prompt_conversation_memory,
                                recalled_memories=recalled_memories,
                                intent=intent,
                            )
                        except (requests.RequestException, ValueError, KeyError, IndexError, json.JSONDecodeError) as exc:
                            openai_error_code = _classify_openai_error(exc)
                            logger.warning("OpenAI request failed; falling back to placeholder response: %s", exc)
                            assistant_payload = build_assistant_placeholder_response(
                                intent,
                                openai_configured=openai_configured,
                                openai_failed=True,
                                openai_error_code=openai_error_code,
                            )
                    else:
                        assistant_payload = build_assistant_placeholder_response(
                            intent,
                            openai_configured=False,
                            openai_error_code=openai_error_code,
                        )

        convo.session_state_json = session_state

        assistant_text = assistant_payload["assistant_text"]
        assistant_metadata = {
            "mode": assistant_payload.get("mode")
            or ("openai" if assistant_payload.get("model_response_json") else "placeholder"),
            "openai_configured": openai_configured,
            "openai_error_code": openai_error_code,
            "model_response_json": assistant_payload.get("model_response_json"),
            "financial_context": financial_context,
            "intent_detection": intent_detection,
            "purchase_advisor_report": purchase_advisor_report,
            "retrieved_memories": recalled_memories,
            "response_style": assistant_payload["response_style"],
            "frontend_hint": assistant_payload["frontend_hint"],
            "action": assistant_payload.get("action"),
            "action_status": assistant_payload.get("status"),
            "created_transaction_id": assistant_payload.get("created_transaction_id"),
            "quick_actions": assistant_payload.get("quick_actions", []),
            "safety_guardrails": assistant_payload.get("safety_guardrails", SAFETY_GUARDRAILS),
            "action_payload": action_payload,
        }

        assistant_msg = Message.objects.create(
            conversation=convo,
            role=MessageRole.ASSISTANT,
            content=assistant_text,
            metadata_json=assistant_metadata,
        )
        _persist_turn_memory(
            conversation=convo,
            user_message=user_msg,
            intent_detection=intent_detection,
        )
        assistant_metadata["conversation_memory"] = refresh_conversation_memory(convo)
        assistant_msg.metadata_json = assistant_metadata
        assistant_msg.save(update_fields=["metadata_json"])

        return Response(
            {
                "user_message": MessageSerializer(user_msg).data,
                "assistant_message": MessageSerializer(assistant_msg).data,
            },
            status=status.HTTP_201_CREATED,
        )


class MessageViewSet(AIViewSetMixin, ReadOnlyModelViewSet):
    serializer_class = MessageSerializer

    def get_queryset(self):
        return Message.objects.filter(conversation__user=self.request.user)


class UserFactViewSet(AIViewSetMixin, ModelViewSet):
    serializer_class = UserFactSerializer

    def get_queryset(self):
        return UserFact.objects.filter(user=self.request.user)

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)

    def perform_update(self, serializer):
        serializer.save(user=self.request.user)