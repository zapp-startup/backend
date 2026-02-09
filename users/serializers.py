from django.contrib.auth import get_user_model
from rest_framework import serializers
from .models import UserProfile, UserPreference, PreferenceValueType

User = get_user_model()

class UserProfileSerializer(serializers.ModelSerializer):
    class Meta:
        model = UserProfile
        fields = [
            "user",
            "display_name",
            "age_range",
            "household_size",
            "location_zip",
            "income_range",
            "financial_goal",
            "risk_tolerance",
            "budget_style",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["created_at", "updated_at"]


class UserPreferenceSerializer(serializers.ModelSerializer):
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
        read_only_fields = ["id", "updated_at"]
        extra_kwargs = {
            "user": {"read_only": True},
            "value_json": {"write_only": True},
        }

    def to_representation(self, instance):
        data = super().to_representation(instance)
        data["value"] = instance.value_json
        data.pop("value_json", None)
        return data

    def validate(self, attrs):
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

        attrs["value_json"] = raw_value
        return attrs

    def create(self, validated_data):
        validated_data.pop("value", None)
        return super().create(validated_data)

    def update(self, instance, validated_data):
        validated_data.pop("value", None)
        return super().update(instance, validated_data)