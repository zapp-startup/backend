from rest_framework import serializers
from .models import Conversation, Message, UserFact


class ConversationSerializer(serializers.ModelSerializer):
    session_state_json = serializers.JSONField(read_only=True)

    class Meta:
        model = Conversation
        fields = (
            "id",
            "user",
            "title",
            "context_type",
            "linked_subscription",
            "linked_item_valuation",
            "created_at",
            "updated_at",
            "summary_text",
            "session_state_json",
            "last_summarized_message_id",
        )
        read_only_fields = (
            "id",
            "user",
            "created_at",
            "updated_at",
            "summary_text",
            "session_state_json",
            "last_summarized_message_id",
        )


class MessageSerializer(serializers.ModelSerializer):
    metadata_json = serializers.JSONField(read_only=True)

    class Meta:
        model = Message
        fields = (
            "id",
            "conversation",
            "role",
            "content",
            "metadata_json",
            "created_at",
        )
        read_only_fields = fields


class UserFactSerializer(serializers.ModelSerializer):
    fact_value_json = serializers.JSONField(required=False)

    class Meta:
        model = UserFact
        fields = (
            "id",
            "user",
            "fact_key",
            "fact_value_json",
            "source",
            "confidence",
            "updated_at",
        )
        read_only_fields = ("id", "user", "updated_at")
