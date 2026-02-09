from rest_framework.viewsets import ModelViewSet
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
    queryset = SubscriptionValuation.objects.all()
    serializer_class = SubscriptionValuationSerializer


class ItemValuationViewSet(ModelViewSet):
    queryset = ItemValuation.objects.all()
    serializer_class = ItemValuationSerializer
