from rest_framework.viewsets import ModelViewSet
from .models import Conversation, Message, UserFact
from .serializers import ConversationSerializer, MessageSerializer, UserFactSerializer


class ConversationViewSet(ModelViewSet):
    queryset = Conversation.objects.all()
    serializer_class = ConversationSerializer


class MessageViewSet(ModelViewSet):
    queryset = Message.objects.all()
    serializer_class = MessageSerializer


class UserFactViewSet(ModelViewSet):
    queryset = UserFact.objects.all()
    serializer_class = UserFactSerializer
