from rest_framework.permissions import IsAuthenticated
from rest_framework.viewsets import ModelViewSet
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import UserProfile, UserPreference
from .serializers import UserProfileSerializer, UserPreferenceSerializer


class UserProfileViewSet(ModelViewSet):
    """
    Exposes the authenticated user's profile.
    - list will return a 1-item list (your profile) if it exists.
    - create will create your profile (OneToOne).
    - retrieve/update/destroy operate only on your own profile.
    """
    serializer_class = UserProfileSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return UserProfile.objects.filter(user=self.request.user)

    def perform_create(self, serializer):
        # Force ownership
        serializer.save(user=self.request.user)

    def get_object(self):
        """
        Since UserProfile uses user as PK (OneToOne primary_key=True),
        the PK is the user's PK. This ensures only self profile is accessible.
        """
        obj = super().get_object()
        if obj.user_id != self.request.user.id:
            raise PermissionDenied("You can only access your own profile.")
        return obj


class UserPreferenceViewSet(ModelViewSet):
    """
    CRUD preferences for the authenticated user.
    Unique per (user, key) as enforced by model constraint.
    """
    serializer_class = UserPreferenceSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return UserPreference.objects.filter(user=self.request.user)

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)

    def get_object(self):
        obj = super().get_object()
        if obj.user_id != self.request.user.id:
            raise PermissionDenied("You can only access your own preferences.")
        return obj


class AuthSessionView(APIView):
    """Returns the authenticated user and decoded Supabase claims."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response(
            {
                "id": request.user.id,
                "username": request.user.username,
                "email": request.user.email,
                "claims": request.auth if isinstance(request.auth, dict) else {},
            }
        )
