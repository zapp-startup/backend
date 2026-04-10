from rest_framework import serializers

from ai.purchase_advisor import ADVISOR_CATEGORIES

from .models import (
    UserRawExplicit,
    UserRawInferred,
    UserComputed,
    UserPreference,
    PreferenceValueType,
)


# ----------------------------
# 1) RAW EXPLICIT (survey/onboarding)
# ----------------------------
class UserRawExplicitSerializer(serializers.ModelSerializer):
    class Meta:
        model = UserRawExplicit
        fields = [
            "user",
            "display_name",
            "dob",
            "age_range",
            "household_size",
            "location_zip",
            "life_stage",
            "employment_type",
            "dependents_count",
            "income_range",
            "monthly_income",
            "monthly_fixed_expenses",
            "financial_goal",
            "risk_tolerance",
            "budget_style",
            "value_priority_cost",
            "value_priority_quality",
            "value_priority_sustainability",
            "self_report_research_habit",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["user", "created_at", "updated_at"]


# ----------------------------
# 2) RAW INFERRED (behavior aggregates)
# Read-only from client; server computes it.
# ----------------------------
class UserRawInferredSerializer(serializers.ModelSerializer):
    class Meta:
        model = UserRawInferred
        fields = [
            "user",
            "window_days",
            "feature_logic_version",
            "data_sufficiency_tier",
            "sparse_signals_json",
            "avg_purchase_price",
            "purchase_price_variance",
            "category_distribution_json",
            "percent_impulsive_purchases",
            "regret_frequency",
            "brand_repetition_rate",
            "late_night_purchase_frequency",
            "avg_decision_time_minutes",
            "active_subscriptions_count",
            "total_subscription_cost",
            "percent_income_spent_on_subscriptions",
            "subscription_usage_frequency_json",
            "cancel_reactivation_frequency",
            "actual_monthly_spending",
            "computed_at",
        ]
        read_only_fields = fields


# ----------------------------
# 3) COMPUTED (processed traits/scores)
# Also read-only to client.
# ----------------------------
class UserComputedSerializer(serializers.ModelSerializer):
    class Meta:
        model = UserComputed
        fields = [
            "user",
            "feature_logic_version",
            "computed_from_inferred_at",
            "spending_personality",
            "product_spending_style",
            "subscription_behavior_type",
            "cost_weight",
            "quality_weight",
            "sustainability_weight",
            "impulse_susceptibility_score",
            "regret_sensitivity",
            "budget_adherence_score",
            "updated_at",
        ]
        read_only_fields = fields


# ----------------------------
# 4) Preferences KV
# ----------------------------
class UserPreferenceSerializer(serializers.ModelSerializer):
    # Public alias: "value" <-> stored in value_json
    value = serializers.JSONField(required=False)

    class Meta:
        model = UserPreference
        fields = [
            "id",
            "user",
            "key",
            "value_type",
            "value",
            "value_json",
            "source",
            "confidence",
            "updated_at",
        ]
        read_only_fields = ["id", "user", "updated_at"]
        extra_kwargs = {
            "value_json": {"write_only": True},
        }

    def to_representation(self, instance):
        data = super().to_representation(instance)
        data["value"] = instance.value_json
        data.pop("value_json", None)
        return data

    def validate(self, attrs):
        key = attrs.get("key", getattr(self.instance, "key", None))
        value_type = attrs.get("value_type", getattr(self.instance, "value_type", None))
        raw_value = attrs.get("value", None)

        if raw_value is None:
            return attrs

        if value_type == PreferenceValueType.BOOL:
            if not isinstance(raw_value, bool):
                raise serializers.ValidationError({"value": "Must be a boolean."})

        elif value_type == PreferenceValueType.INT:
            if isinstance(raw_value, bool) or not isinstance(raw_value, int):
                raise serializers.ValidationError({"value": "Must be an integer."})

        elif value_type == PreferenceValueType.FLOAT:
            if isinstance(raw_value, bool) or not isinstance(raw_value, (int, float)):
                raise serializers.ValidationError({"value": "Must be a number."})

        elif value_type == PreferenceValueType.STRING:
            if not isinstance(raw_value, str):
                raise serializers.ValidationError({"value": "Must be a string."})

        elif value_type == PreferenceValueType.JSON:
            if not isinstance(raw_value, (dict, list)):
                raise serializers.ValidationError({"value": "Must be an object or array."})

        else:
            raise serializers.ValidationError({"value_type": "Invalid value_type."})

        if key == "purchase_advisor_logic":
            if value_type != PreferenceValueType.JSON or not isinstance(raw_value, dict):
                raise serializers.ValidationError({
                    "value": "purchase_advisor_logic must be a JSON object with advisor settings.",
                })
            if "enabled" not in raw_value or not isinstance(raw_value["enabled"], bool):
                raise serializers.ValidationError({"value": "purchase_advisor_logic.enabled must be a boolean."})
            if "lookback_days" in raw_value and (not isinstance(raw_value["lookback_days"], int) or raw_value["lookback_days"] < 1):
                raise serializers.ValidationError({"value": "purchase_advisor_logic.lookback_days must be a positive integer."})
            if "overspending_ratio_threshold" in raw_value and (
                isinstance(raw_value["overspending_ratio_threshold"], bool)
                or not isinstance(raw_value["overspending_ratio_threshold"], (int, float))
                or raw_value["overspending_ratio_threshold"] <= 0
            ):
                raise serializers.ValidationError({
                    "value": "purchase_advisor_logic.overspending_ratio_threshold must be a positive number.",
                })
            if "focus_categories" in raw_value:
                if not isinstance(raw_value["focus_categories"], list):
                    raise serializers.ValidationError({"value": "purchase_advisor_logic.focus_categories must be an array."})
                invalid_categories = [
                    category for category in raw_value["focus_categories"]
                    if not isinstance(category, str) or category not in ADVISOR_CATEGORIES
                ]
                if invalid_categories:
                    raise serializers.ValidationError({
                        "value": f"purchase_advisor_logic.focus_categories contains unsupported categories: {invalid_categories}.",
                    })

        # store into model field
        attrs["value_json"] = raw_value
        return attrs

    def create(self, validated_data):
        validated_data.pop("value", None)
        return super().create(validated_data)

    def update(self, instance, validated_data):
        validated_data.pop("value", None)
        return super().update(instance, validated_data)
