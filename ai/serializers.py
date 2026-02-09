from rest_framework import serializers
from .models import Conversation, Message, UserFact


class ConversationSerializer(serializers.ModelSerializer):
    class Meta:
        model = Conversation
        fields = "__all__"


class MessageSerializer(serializers.ModelSerializer):
    class Meta:
        model = Message
        fields = "__all__"


class UserFactSerializer(serializers.ModelSerializer):
    class Meta:
        model = UserFact
        fields = "__all__"
