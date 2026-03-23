from collections import defaultdict
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.shortcuts import get_object_or_404
from django.utils import timezone
from subscriptions.models import Subscription
from transactions.models import Transaction, TransactionDirection
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.viewsets import ModelViewSet

from .models import Conversation, Message, UserFact, MessageRole, ConversationContext
from .serializers import ConversationSerializer, MessageSerializer, UserFactSerializer
from .openai_config import get_openai_api_key
from .intents import classify_intent
from .purchase_advisor import LOCAL_TO_ADVISOR_CATEGORY, extract_requested_category

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

def build_financial_context(user, *, transaction_limit=5, subscription_limit=5):
    recent_transactions = list(
        Transaction.objects.filter(user=user)
        .select_related("merchant")
        .order_by("-occurred_at")[:transaction_limit]
    )
    active_subscriptions = list(
        Subscription.objects.filter(user=user, status="active")
        .select_related("merchant")
        .order_by("renewal_date", "id")[:subscription_limit]
    )

    spend_amount = sum(
        transaction.amount
        for transaction in recent_transactions
        if transaction.direction == TransactionDirection.SPEND
    )

    return {
        "recent_transactions": [_format_recent_transaction(tx) for tx in recent_transactions],
        "active_subscriptions": [_format_subscription(sub) for sub in active_subscriptions],
        "summary": {
            "transaction_count": len(recent_transactions),
            "active_subscription_count": len(active_subscriptions),
            "recent_spend_total": str(spend_amount),
        },
    }


SHORT_TERM_MEMORY_MESSAGE_LIMIT = 6
SUMMARY_TRIGGER_MESSAGE_COUNT = 8
SUMMARY_MAX_TURNS = 12


def _summarize_messages(messages):
    summary_lines = []
    for message in messages[:SUMMARY_MAX_TURNS]:
        role = message.role.capitalize()
        content = " ".join(message.content.split())
        if len(content) > 140:
            content = f"{content[:137]}..."
        summary_lines.append(f"{role}: {content}")
    return "\n".join(summary_lines)


def _extract_session_state(conversation, recent_messages):
    open_loops = []
    last_user_message = None
    mentioned_entities = []

    for message in recent_messages:
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


def build_conversation_memory(conversation, *, message_limit=SHORT_TERM_MEMORY_MESSAGE_LIMIT):
    recent_messages = list(
        conversation.messages.order_by("-created_at", "-id")[:message_limit]
    )
    recent_messages.reverse()
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

    if len(all_messages) >= SUMMARY_TRIGGER_MESSAGE_COUNT:
        messages_to_summarize = all_messages[:-SHORT_TERM_MEMORY_MESSAGE_LIMIT]
        if messages_to_summarize:
            conversation.summary_text = _summarize_messages(messages_to_summarize)
            conversation.last_summarized_message_id = messages_to_summarize[-1].id
            update_fields.extend(["summary_text", "last_summarized_message_id"])

    conversation.session_state_json = _extract_session_state(conversation, all_messages[-SHORT_TERM_MEMORY_MESSAGE_LIMIT:])
    conversation.save(update_fields=update_fields)
    return build_conversation_memory(conversation)


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


def build_assistant_placeholder_response(intent: str, openai_configured: bool) -> dict:
    base_suffix = (
        "OpenAI call wiring is the next step."
        if openai_configured
        else "OpenAI key is not configured yet."
    )
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
            "frontend_hint": "Display page navigation actions for transactions, subscriptions, and item valuations.",
        },
        "ask": {
            "message": (
                "✅ I understand this as a question. "
                "I can answer directly and ask one follow-up only if critical details are missing."
            ),
            "response_style": "direct_answer",
            "frontend_hint": "Render a normal assistant reply view.",
        },
        "edit": {
            "message": (
                "✅ I understand this as an edit request. "
                "I should return a revised version of the user-provided text with minimal extra commentary."
            ),
            "response_style": "transformation",
            "frontend_hint": "Offer side-by-side/original-vs-rewrite UI.",
        },
        "recommend": {
            "message": (
                "✅ I understand this as a recommendation request. "
                "I should provide ranked options, the reasoning behind each option, and a best next action."
            ),
            "response_style": "ranked_recommendations",
            "frontend_hint": "Show recommendation cards with rationale and confidence.",
        },
        "summarize": {
            "message": (
                "✅ I understand this as a summarization request. "
                "I should return concise key points and optional action items."
            ),
            "response_style": "summary",
            "frontend_hint": "Use compact bullets and collapse long source text by default.",
        },
    }

    template = intent_templates.get(intent, intent_templates["ask"])
    return {
        "assistant_text": f"{template['message']} ({base_suffix}){safety_suffix}",
        "response_style": template["response_style"],
        "frontend_hint": template["frontend_hint"],
        "safety_guardrails": SAFETY_GUARDRAILS,
    }


def get_dev_user(request):
    """
    Temporary dev auth:
    Frontend sends header: X-Dev-User: seed_user_0
    """
    username = request.headers.get("X-Dev-User")
    if not username:
        return None
    User = get_user_model()
    try:
        return User.objects.get(username=username)
    except User.DoesNotExist:
        return None


class ConversationViewSet(ModelViewSet):
    serializer_class = ConversationSerializer
    throttle_scope = "ai"

    def get_queryset(self):
        user = get_dev_user(self.request)
        if not user:
            return Conversation.objects.none()
        return Conversation.objects.filter(user=user)

    def create(self, request, *args, **kwargs):
        user = get_dev_user(request)
        if not user:
            return Response(
                {"detail": "Missing or invalid X-Dev-User header"},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        # ✅ only accept what you need
        context_type = request.data.get("context_type", ConversationContext.GENERAL)
        title = request.data.get("title")
        linked_subscription_id = request.data.get("linked_subscription")
        linked_item_valuation_id = request.data.get("linked_item_valuation")

        convo = Conversation.objects.create(
            user=user,
            context_type=context_type,
            title=title,
            linked_subscription_id=linked_subscription_id,
            linked_item_valuation_id=linked_item_valuation_id,
        )

        return Response({"conversation_id": convo.id}, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["get", "post"], url_path="messages")
    def messages(self, request, pk=None):
        """
        GET  /api/ai/conversations/<id>/messages/
        POST /api/ai/conversations/<id>/messages/  body: { "content": "..." }
        """
        user = get_dev_user(request)
        if not user:
            return Response(
                {"detail": "Missing or invalid X-Dev-User header"},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        convo = get_object_or_404(Conversation, id=pk, user=user)

        if request.method.lower() == "get":
            qs = Message.objects.filter(conversation=convo).order_by("created_at")
            return Response(MessageSerializer(qs, many=True).data)

        # POST: create user msg + placeholder assistant msg
        content = request.data.get("content", "")
        content = (content or "").strip()
        if not content:
            return Response({"detail": "content is required"}, status=status.HTTP_400_BAD_REQUEST)

        intent_detection = classify_intent(content)

        user_msg = Message.objects.create(
            conversation=convo,
            role=MessageRole.USER,
            content=content,
            metadata_json={"intent_detection": intent_detection},
        )

        openai_configured = bool(get_openai_api_key())
        financial_context = build_financial_context(user)
        purchase_advisor_report = build_purchase_advisor_report(user, content)
        intent = intent_detection["intent"]
        if intent == "record_transaction":
            assistant_placeholder = _build_record_transaction_response()
        else:
            assistant_placeholder = build_assistant_placeholder_response(intent, openai_configured)

        # placeholder assistant response for now (OpenAI integration still pending)
        assistant_text = assistant_placeholder["assistant_text"]

        assistant_msg = Message.objects.create(
            conversation=convo,
            role=MessageRole.ASSISTANT,
            content=assistant_text,
            metadata_json={
                "mode": "placeholder",
                "openai_configured": openai_configured,
                "financial_context": financial_context,
                "conversation_memory": refresh_conversation_memory(convo),
                "intent_detection": intent_detection,
                "purchase_advisor_report": purchase_advisor_report,
                "response_style": assistant_placeholder["response_style"],
                "frontend_hint": assistant_placeholder["frontend_hint"],
                "action": assistant_placeholder.get("action"),
                "action_status": assistant_placeholder.get("status"),
                "created_transaction_id": assistant_placeholder.get("created_transaction_id"),
                "quick_actions": assistant_placeholder.get("quick_actions", []),
                "safety_guardrails": assistant_placeholder.get("safety_guardrails", SAFETY_GUARDRAILS),
            },
        )

        return Response(
            {
                "user_message": MessageSerializer(user_msg).data,
                "assistant_message": MessageSerializer(assistant_msg).data,
            },
            status=status.HTTP_201_CREATED,
        )


class MessageViewSet(ModelViewSet):
    serializer_class = MessageSerializer
    throttle_scope = "ai"

    def get_queryset(self):
        # Optional: keep this for debugging, but still lock down by user
        user = get_dev_user(self.request)
        if not user:
            return Message.objects.none()
        return Message.objects.filter(conversation__user=user)


class UserFactViewSet(ModelViewSet):
    serializer_class = UserFactSerializer
    throttle_scope = "ai"

    def get_queryset(self):
        user = get_dev_user(self.request)
        if not user:
            return UserFact.objects.none()
        return UserFact.objects.filter(user=user)

    def perform_create(self, serializer):
        user = get_dev_user(self.request)
        if not user:
            raise PermissionError("Missing or invalid X-Dev-User header")
        serializer.save(user=user)
