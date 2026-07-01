from rest_framework import serializers
from rest_framework.validators import UniqueValidator

from .models import WaitlistSignup


class WaitlistSignupSerializer(serializers.ModelSerializer):
    email = serializers.EmailField()

    class Meta:
        model = WaitlistSignup
        fields = ["id", "name", "email", "source", "created_at"]
        read_only_fields = ["id", "created_at"]

    def get_fields(self):
        fields = super().get_fields()
        fields["email"].validators = [
            validator
            for validator in fields["email"].validators
            if not isinstance(validator, UniqueValidator)
        ]
        return fields

    def validate_name(self, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise serializers.ValidationError("Name is required.")
        return cleaned

    def validate_email(self, value: str) -> str:
        cleaned = value.strip().lower()
        if not cleaned:
            raise serializers.ValidationError("Email is required.")
        return cleaned
