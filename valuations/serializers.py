from rest_framework import serializers
from .models import ValuationModelVersion, SubscriptionValuation, ItemValuation


class ValuationModelVersionSerializer(serializers.ModelSerializer):
    class Meta:
        model = ValuationModelVersion
        fields = "__all__"


class SubscriptionValuationSerializer(serializers.ModelSerializer):
    class Meta:
        model = SubscriptionValuation
        fields = "__all__"


class ItemValuationSerializer(serializers.ModelSerializer):
    class Meta:
        model = ItemValuation
        fields = "__all__"
