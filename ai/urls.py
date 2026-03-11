from rest_framework.routers import DefaultRouter
from .views import ConversationViewSet, MessageViewSet, UserFactViewSet

router = DefaultRouter()
router.include_format_suffixes = False
router.register(r"conversations", ConversationViewSet, basename="conversations")
router.register(r"messages", MessageViewSet, basename="messages")
router.register(r"user-facts", UserFactViewSet, basename="user-facts")

urlpatterns = router.urls
