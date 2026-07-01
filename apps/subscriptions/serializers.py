from rest_framework import serializers
from .models import Merchant, Subscription


class MerchantSerializer(serializers.ModelSerializer):
    class Meta:
        model = Merchant
        # Explicit display allowlist. Internal/operator fields (website_domain
        # used for matching, subscription_eligibility for synthetic data, and
        # the timestamps) are intentionally excluded from the catalog response.
        fields = ["id", "name", "category"]


class SubscriptionSerializer(serializers.ModelSerializer):
    """
    `latest_value_score` is the latest SubscriptionValuation.personal_value_score when the
    view annotates `latest_valuation_score`. Feedback→numeric fields are not used here until that pipeline ships.
    """

    latest_value_score = serializers.SerializerMethodField()

    class Meta:
        model = Subscription
        fields = [f.name for f in Subscription._meta.fields] + ["latest_value_score"]
        read_only_fields = ("user",)

    def get_latest_value_score(self, obj):
        return getattr(obj, "latest_valuation_score", None)
