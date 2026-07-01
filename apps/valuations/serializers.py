from rest_framework import serializers
from .models import (
    ItemValuation,
    SubscriptionValuation,
    TransactionValuation,
    ValuationModelVersion,
)


class ValuationModelVersionSerializer(serializers.ModelSerializer):
    class Meta:
        model = ValuationModelVersion
        fields = "__all__"


class SubscriptionValuationSerializer(serializers.ModelSerializer):
    class Meta:
        model = SubscriptionValuation
        fields = "__all__"
        read_only_fields = ("user",)


class ItemValuationSerializer(serializers.ModelSerializer):
    class Meta:
        model = ItemValuation
        fields = "__all__"
        read_only_fields = ("user",)


class TransactionValuationSerializer(serializers.ModelSerializer):
    class Meta:
        model = TransactionValuation
        fields = "__all__"
        read_only_fields = ("user",)
