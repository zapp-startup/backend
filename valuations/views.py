from rest_framework.permissions import IsAuthenticated
from rest_framework.viewsets import ModelViewSet

from gamification.services import award_points_for_item_valuation, award_points_for_subscription_valuation

from .models import ValuationModelVersion, SubscriptionValuation, ItemValuation
from .serializers import (
    ValuationModelVersionSerializer,
    SubscriptionValuationSerializer,
    ItemValuationSerializer,
)


class ValuationModelVersionViewSet(ModelViewSet):
    queryset = ValuationModelVersion.objects.all()
    serializer_class = ValuationModelVersionSerializer


class SubscriptionValuationViewSet(ModelViewSet):
    serializer_class = SubscriptionValuationSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        queryset = SubscriptionValuation.objects.filter(user=self.request.user).select_related("subscription", "model_version")
        subscription_id = self.request.query_params.get("subscription")
        if subscription_id is not None:
            queryset = queryset.filter(subscription_id=subscription_id)
        return queryset

    def perform_create(self, serializer):
        valuation = serializer.save(user=self.request.user)
        award_points_for_subscription_valuation(valuation)


class ItemValuationViewSet(ModelViewSet):
    serializer_class = ItemValuationSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return ItemValuation.objects.filter(user=self.request.user).select_related("model_version")

    def perform_create(self, serializer):
        valuation = serializer.save(user=self.request.user)
        award_points_for_item_valuation(valuation)
