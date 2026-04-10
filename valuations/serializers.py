from rest_framework import serializers
from .models import Recommendation, ValuationModelVersion, SubscriptionValuation, ItemValuation


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
    def validate(self, attrs):
        attrs = super().validate(attrs)

        if not attrs.get("model_version"):
            model_version, _ = ValuationModelVersion.objects.get_or_create(
                name="item_value",
                version="manual-entry-v1",
                defaults={"description": "Default version for lightweight manual item valuations."},
            )
            attrs["model_version"] = model_version

        observed_price = attrs.get("observed_price")
        if attrs.get("personal_value_score") is None:
            score = 95
            if observed_price is not None:
                price = float(observed_price)
                score = round(max(35, min(120, 115 - (price / 4.0))))
            attrs["personal_value_score"] = score

        if not attrs.get("recommendation"):
            score = attrs["personal_value_score"]
            if score >= 98:
                attrs["recommendation"] = Recommendation.BUY
            elif score <= 55:
                attrs["recommendation"] = Recommendation.SKIP
            else:
                attrs["recommendation"] = Recommendation.WAIT

        if attrs.get("confidence") is None:
            attrs["confidence"] = 0.35

        evidence_json = attrs.get("evidence_json") or {}
        if not evidence_json:
            evidence_json = {
                "source": "manual_entry_bootstrap",
                "observed_price": float(observed_price) if observed_price is not None else None,
            }
        attrs["evidence_json"] = evidence_json

        reasoning_json = attrs.get("reasoning_json") or {}
        if not reasoning_json:
            reasoning_json = {
                "summary": "Bootstrap valuation created from manual entry.",
                "next_step": "Add more transaction feedback to replace this with a personalized model score.",
            }
        attrs["reasoning_json"] = reasoning_json

        return attrs

    class Meta:
        model = ItemValuation
        fields = "__all__"
        read_only_fields = ("user",)
        extra_kwargs = {
            "model_version": {"required": False},
            "personal_value_score": {"required": False},
            "recommendation": {"required": False},
            "confidence": {"required": False},
            "evidence_json": {"required": False},
            "reasoning_json": {"required": False},
        }
