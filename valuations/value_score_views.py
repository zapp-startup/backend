from __future__ import annotations

from django.db.models import OuterRef, Subquery
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import SubscriptionValuation
from .serializers import SubscriptionValuationSerializer
from .services.value_score_orchestrator import run_value_scores_for_user


class ValueScoreMeView(APIView):
    """Latest subscription valuations for the current user (value-score pipeline)."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        qs = (
            SubscriptionValuation.objects.filter(user=request.user)
            .select_related("subscription", "model_version")
            .order_by("-period_end", "-created_at")
        )
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
