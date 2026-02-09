from rest_framework.permissions import IsAuthenticated, AllowAny
from rest_framework.viewsets import ModelViewSet, ReadOnlyModelViewSet

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
        serializer.save(user=self.request.user)
