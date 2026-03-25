from rest_framework.permissions import IsAuthenticated, AllowAny
from rest_framework.viewsets import ModelViewSet, ReadOnlyModelViewSet

from gamification.services import (
    award_points_for_subscription_added,
    award_points_for_subscription_cancelled,
    award_points_for_subscription_paused,
)

from .models import Merchant, Subscription
from .serializers import MerchantSerializer, SubscriptionSerializer


class MerchantViewSet(ReadOnlyModelViewSet):
    """
    Merchants are canonical/global. Usually read-only for normal users.
    If you want users to create merchants, switch to ModelViewSet + permissions.
    """
    queryset = Merchant.objects.all()
    serializer_class = MerchantSerializer
    permission_classes = [AllowAny]  # or IsAuthenticated if you want locked down


class SubscriptionViewSet(ModelViewSet):
    serializer_class = SubscriptionSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        # Users can only see their own subscriptions
        return Subscription.objects.filter(user=self.request.user).select_related("merchant")

    def perform_create(self, serializer):
        # Force ownership
        subscription = serializer.save(user=self.request.user)
        award_points_for_subscription_added(subscription)

    def perform_update(self, serializer):
        previous = self.get_object()
        previous_status = previous.status
        subscription = serializer.save(user=self.request.user)

        if previous_status != subscription.status:
            if subscription.status == "canceled":
                award_points_for_subscription_cancelled(subscription)
            elif subscription.status == "paused":
                award_points_for_subscription_paused(subscription)
