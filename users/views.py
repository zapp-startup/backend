from rest_framework.viewsets import ModelViewSet
from rest_framework.permissions import IsAuthenticated
from rest_framework.exceptions import NotFound

from .models import UserProfile, UserPreference
from .serializers import UserProfileSerializer, UserPreferenceSerializer

class UserProfileViewSet(ModelViewSet):
    """
    React + DRF pattern:
    - Only ever expose the authenticated user's profile.
    - Ignore any user id coming from the client.
    """
    serializer_class = UserProfileSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return UserProfile.objects.select_related("user").filter(user=self.request.user)

    def get_object(self):
        obj = self.get_queryset().first()
        if not obj:
            raise NotFound("Profile not found.")
        return obj

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)


class UserPreferenceViewSet(ModelViewSet):
    """
    CRUD for preferences, scoped to authenticated user.
    """
    serializer_class = UserPreferenceSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return (
            UserPreference.objects
            .filter(user=self.request.user)
            .order_by("-updated_at")
        )

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)