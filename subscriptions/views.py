from rest_framework import viewsets
from .models import Subscription
from .serializers import SubscriptionSerializer

class SubscriptionViewSet(viewsets.ModelViewSet):
    queryset = Subscription.objects.all().order_by("id")
    serializer_class = SubscriptionSerializer
