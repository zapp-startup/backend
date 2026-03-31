from rest_framework import serializers

from .models import AuditEvent


class AuditEventIngestSerializer(serializers.Serializer):
    event_name = serializers.CharField(max_length=128)
    occurred_at = serializers.DateTimeField(required=False)
    outcome = serializers.ChoiceField(choices=AuditEvent.Outcome.choices)
    actor_id = serializers.CharField(max_length=128, required=False, allow_blank=True, allow_null=True)
    actor_type = serializers.ChoiceField(
        choices=AuditEvent.ActorType.choices,
        required=False,
    )
    source_system = serializers.CharField(max_length=64)
    request_id = serializers.CharField(max_length=128, required=False, allow_blank=True)
    action = serializers.CharField(max_length=64, required=False, allow_blank=True)
    resource_type = serializers.CharField(max_length=64, required=False, allow_blank=True)
    resource_id = serializers.CharField(max_length=128, required=False, allow_blank=True, allow_null=True)
    route = serializers.CharField(max_length=255, required=False, allow_blank=True)
    method = serializers.CharField(max_length=16, required=False, allow_blank=True)
    status_code = serializers.IntegerField(required=False, min_value=100, max_value=599, allow_null=True)
    error_code = serializers.CharField(max_length=64, required=False, allow_blank=True)
    error_message = serializers.CharField(required=False, allow_blank=True)
    metadata = serializers.JSONField(required=False)

    def create(self, validated_data):
        request = self.context["request"]
        user = request.user if getattr(request.user, "is_authenticated", False) else None
        actor_id = str(getattr(user, "supabase_uid", "") or getattr(user, "pk", "") or "").strip()

        return AuditEvent.objects.create(
            user=user,
            event_name=validated_data["event_name"],
            outcome=validated_data["outcome"],
            actor_id=actor_id,
            actor_type=AuditEvent.ActorType.USER if user else AuditEvent.ActorType.ANONYMOUS,
            source_system=validated_data["source_system"],
            request_id=validated_data.get("request_id", ""),
            action=validated_data.get("action", ""),
            resource_type=validated_data.get("resource_type", ""),
            resource_id=str(validated_data.get("resource_id", "") or ""),
            route=validated_data.get("route", ""),
            method=(validated_data.get("method", "") or "").upper(),
            status_code=validated_data.get("status_code"),
            error_code=validated_data.get("error_code", ""),
            error_message=validated_data.get("error_message", ""),
            metadata=validated_data.get("metadata") or {},
        )
