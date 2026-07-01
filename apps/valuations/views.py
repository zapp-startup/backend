from rest_framework.permissions import IsAdminUser
from rest_framework.viewsets import ModelViewSet, ReadOnlyModelViewSet

from apps.gamification.services import award_points_for_item_valuation, award_points_for_subscription_valuation
from apps.users.permissions import IsFullyAuthenticated

from .models import ValuationModelVersion, SubscriptionValuation, ItemValuation, TransactionValuation
from .serializers import (
    ValuationModelVersionSerializer,
    SubscriptionValuationSerializer,
    ItemValuationSerializer,
    TransactionValuationSerializer,
)


class ValuationModelVersionViewSet(ModelViewSet):
    # Model versions are operator-owned metadata, not per-user data. Restrict to
    # staff so the catalog of scoring models is not exposed (or mutable) to end
    # users. Previously this viewset declared no permission_classes and, with no
    # project-wide default, was world-readable/writable.
    queryset = ValuationModelVersion.objects.all()
    serializer_class = ValuationModelVersionSerializer
    permission_classes = [IsAdminUser]


class SubscriptionValuationViewSet(ModelViewSet):
    serializer_class = SubscriptionValuationSerializer
    permission_classes = [IsFullyAuthenticated]

    def get_queryset(self):
        return SubscriptionValuation.objects.filter(user=self.request.user).select_related("subscription", "model_version")

    def perform_create(self, serializer):
        valuation = serializer.save(user=self.request.user)
        award_points_for_subscription_valuation(valuation)


class ItemValuationViewSet(ModelViewSet):
    serializer_class = ItemValuationSerializer
    permission_classes = [IsFullyAuthenticated]

    def get_queryset(self):
        return ItemValuation.objects.filter(user=self.request.user).select_related("model_version")

    def perform_create(self, serializer):
        valuation = serializer.save(user=self.request.user)
        award_points_for_item_valuation(valuation)


class TransactionValuationViewSet(ReadOnlyModelViewSet):
    serializer_class = TransactionValuationSerializer
    permission_classes = [IsFullyAuthenticated]

    def get_queryset(self):
        return TransactionValuation.objects.filter(user=self.request.user).select_related("transaction", "model_version")
