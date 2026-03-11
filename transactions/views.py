from rest_framework.permissions import IsAuthenticated
from rest_framework.viewsets import ModelViewSet

from gamification.services import award_points_for_same_day_reflection, award_points_for_transaction

from .models import Transaction, TransactionReflection
from .serializers import TransactionReflectionSerializer, TransactionSerializer


class TransactionViewSet(ModelViewSet):
    serializer_class = TransactionSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        # Users can only see their own transactions
        return (
            Transaction.objects
            .filter(user=self.request.user)
            .select_related("merchant", "subscription")
        )

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
