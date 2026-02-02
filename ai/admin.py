from django.contrib import admin
from .models import Conversation, Message, UserFact

admin.site.register(Conversation)
admin.site.register(Message)
admin.site.register(UserFact)