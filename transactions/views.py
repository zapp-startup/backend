from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.viewsets import GenericViewSet, ModelViewSet

from gamification.services import (
    award_points_for_same_day_reflection,
    award_points_for_transaction,
)

from .feedback_candidates import get_feedback_candidates
from .models import Transaction, TransactionReflection
from .serializers import TransactionReflectionSerializer, TransactionSerializer

class TransactionViewSet(ModelViewSet):
    serializer_class = TransactionSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        qs = (
            Transaction.objects
            .filter(user=self.request.user)
            .select_related("merchant", "subscription")
        )
        category = self.request.query_params.get("category")
        direction = self.request.query_params.get("direction")
        date_from = self.request.query_params.get("date_from")
        date_to = self.request.query_params.get("date_to")

        if category:
            qs = qs.filter(category=category)
        if direction:
            qs = qs.filter(direction=direction)
        if date_from:
            qs = qs.filter(occurred_at__date__gte=date_from)
        if date_to:
            qs = qs.filter(occurred_at__date__lte=date_to)

        limit = self.request.query_params.get("limit")
        if limit:
            qs = qs[:int(limit)]
        return qs

    @action(detail=False, methods=["get"], url_path="feedback-candidates")
    def feedback_candidates(self, request):
        """
        Return the top N transaction candidates for feedback.
        Prioritizes high-signal discretionary purchases over routine expenses.
        """
        top_n = request.query_params.get("n", "2")
        try:
            top_n = min(max(1, int(top_n)), 10)
        except ValueError:
            top_n = 2
        days = request.query_params.get("days", "90")
        try:
            days = min(max(1, int(days)), 365)
        except ValueError:
            days = 90
        candidates = get_feedback_candidates(
            user=request.user,
            days_window=days,
            top_n=top_n,
        )
        return Response(candidates)

    def perform_create(self, serializer):
        # Force ownership
        transaction = serializer.save(user=self.request.user)
        award_points_for_transaction(transaction)


class TransactionReflectionViewSet(
    mixins.CreateModelMixin,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    GenericViewSet,
):
    serializer_class = TransactionReflectionSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return (
            TransactionReflection.objects
            .filter(user=self.request.user)
            .select_related("transaction")
        )

    def perform_create(self, serializer):
        reflection = serializer.save(user=self.request.user)
        award_points_for_same_day_reflection(reflection)
