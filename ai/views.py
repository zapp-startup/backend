from django.contrib.auth import get_user_model
from django.shortcuts import get_object_or_404
from subscriptions.models import Subscription
from transactions.models import Transaction, TransactionDirection
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.viewsets import ModelViewSet

from .models import Conversation, Message, UserFact, MessageRole, ConversationContext
from .serializers import ConversationSerializer, MessageSerializer, UserFactSerializer
from .openai_config import get_openai_api_key


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

        user_msg = Message.objects.create(
            conversation=convo,
            role=MessageRole.USER,
            content=content,
            metadata_json={},
        )

        openai_configured = bool(get_openai_api_key())
        financial_context = build_financial_context(user)

        # placeholder assistant response for now (OpenAI integration still pending)
        if openai_configured:
            assistant_text = "✅ Got it — I saved that and fetched your recent transactions/subscriptions for context. (OpenAI call wiring is the next step.)"
        else:
            assistant_text = "✅ Got it — I saved that and fetched your recent transactions/subscriptions for context. (OpenAI key is not configured yet.)"

        assistant_msg = Message.objects.create(
            conversation=convo,
            role=MessageRole.ASSISTANT,
            content=assistant_text,
            metadata_json={
                "mode": "placeholder",
                "openai_configured": openai_configured,
                "financial_context": financial_context,
            },
        )

        convo.save(update_fields=["updated_at"])

        return Response(
            {
                "user_message": MessageSerializer(user_msg).data,
                "assistant_message": MessageSerializer(assistant_msg).data,
            },
            status=status.HTTP_201_CREATED,
        )


class MessageViewSet(ModelViewSet):
    serializer_class = MessageSerializer

    def get_queryset(self):
        # Optional: keep this for debugging, but still lock down by user
        user = get_dev_user(self.request)
        if not user:
            return Message.objects.none()
        return Message.objects.filter(conversation__user=user)


class UserFactViewSet(ModelViewSet):
    serializer_class = UserFactSerializer

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
