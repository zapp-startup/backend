from rest_framework.permissions import IsAuthenticated
from rest_framework.viewsets import ModelViewSet

from .models import Transaction
from .serializers import TransactionSerializer


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
        serializer.save(user=self.request.user)
