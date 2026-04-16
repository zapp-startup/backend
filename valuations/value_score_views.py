from __future__ import annotations

from django.db.models import OuterRef, Subquery
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from transactions.models import Transaction

from .models import SubscriptionValuation
from .serializers import SubscriptionValuationSerializer, TransactionValuationSerializer
from .services.persistence import latest_subscription_valuations_queryset, latest_transaction_valuations_queryset
from .services.transaction_value_score import persist_transaction_value_score
from .services.value_score_inference import ValueScoreModelNotAvailable
from .services.value_score_orchestrator import run_value_scores_for_user


class ValueScoreMeView(APIView):
    """Latest subscription valuations for the current user (value-score pipeline)."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        qs = latest_subscription_valuations_queryset(request.user).order_by("-period_end", "-created_at")
        sub_id = request.query_params.get("subscription")
        if sub_id:
            qs = qs.filter(subscription_id=sub_id)
        ser = SubscriptionValuationSerializer(qs[:200], many=True)
        return Response(ser.data)


class ValueScoreRecomputeView(APIView):
    """Run platform_bundle value-score model and persist SubscriptionValuation rows."""

    permission_classes = [IsAuthenticated]

    def post(self, request):
        raw_ids = request.data.get("subscription_ids")
        ids = None
        if isinstance(raw_ids, list):
            try:
                ids = [int(x) for x in raw_ids]
            except (TypeError, ValueError):
                return Response(
                    {"detail": "subscription_ids must be a list of integers."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        payload = run_value_scores_for_user(request.user.id, subscription_ids=ids)
        if not payload.get("ok"):
            return Response(
                {"detail": payload.get("error", "Value score model unavailable."), "valuations": []},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        return Response(payload, status=status.HTTP_200_OK)


class TransactionValuationListView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        qs = latest_transaction_valuations_queryset(request.user).order_by("-created_at")
        transaction_id = request.query_params.get("transaction_id")
        if transaction_id:
            qs = qs.filter(transaction_id=transaction_id)
        serializer = TransactionValuationSerializer(qs[:200], many=True)
        return Response(serializer.data)


class TransactionValuationRecomputeView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        raw_ids = request.data.get("transaction_ids")
        if not isinstance(raw_ids, list) or not raw_ids:
            return Response(
                {"detail": "transaction_ids must be a non-empty list of integers."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        valuations = []
        for raw_id in raw_ids:
            try:
                transaction = Transaction.objects.get(id=int(raw_id), user=request.user)
            except (TypeError, ValueError, Transaction.DoesNotExist):
                continue
            try:
                valuation = persist_transaction_value_score(transaction)
            except ValueScoreModelNotAvailable as exc:
                return Response({"detail": str(exc)}, status=status.HTTP_503_SERVICE_UNAVAILABLE)
            valuations.append(valuation)
        serializer = TransactionValuationSerializer(valuations, many=True)
        return Response({"ok": True, "valuations": serializer.data}, status=status.HTTP_200_OK)


def annotate_subscriptions_with_latest_score(queryset):
    """Annotate Subscription queryset with latest_valuation_score from SubscriptionValuation."""
    sq = (
        SubscriptionValuation.objects.filter(
            subscription_id=OuterRef("pk"),
            user_id=OuterRef("user_id"),
        )
        .order_by("-period_end", "-created_at")
        .values("personal_value_score")[:1]
    )
    return queryset.annotate(latest_valuation_score=Subquery(sq))
