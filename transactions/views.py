from rest_framework.permissions import IsAuthenticated
from rest_framework.viewsets import ModelViewSet

from gamification.services import award_points_for_same_day_reflection, award_points_for_transaction

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

    def perform_create(self, serializer):
        # Force ownership
        transaction = serializer.save(user=self.request.user)
        award_points_for_transaction(transaction)


class TransactionReflectionViewSet(ModelViewSet):
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
