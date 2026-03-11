from rest_framework.permissions import IsAuthenticated
from rest_framework.viewsets import ModelViewSet, ReadOnlyModelViewSet
from rest_framework.exceptions import PermissionDenied

from gamification.services import award_points_for_onboarding

from .models import UserRawExplicit, UserRawInferred, UserComputed, UserPreference
from .serializers import (
    UserRawExplicitSerializer,
    UserRawInferredSerializer,
    UserComputedSerializer,
    UserPreferenceSerializer,
)


class UserRawExplicitViewSet(ModelViewSet):
    """
    Editable explicit profile (survey/onboarding).
    """
    serializer_class = UserRawExplicitSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return UserRawExplicit.objects.filter(user=self.request.user)

    def perform_create(self, serializer):
        profile = serializer.save(user=self.request.user)
        award_points_for_onboarding(profile.user)

    def get_object(self):
        obj = super().get_object()
        if obj.user_id != self.request.user.id:
            raise PermissionDenied("You can only access your own profile.")
        return obj


class UserRawInferredViewSet(ReadOnlyModelViewSet):
    """
    Read-only inferred snapshot (system computed).
    """
    serializer_class = UserRawInferredSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return UserRawInferred.objects.filter(user=self.request.user)

    def get_object(self):
        obj = super().get_object()
        if obj.user_id != self.request.user.id:
            raise PermissionDenied("You can only access your own inferred data.")
        return obj


class UserComputedViewSet(ReadOnlyModelViewSet):
    """
    Read-only computed traits/scores (system computed).
    """
    serializer_class = UserComputedSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return UserComputed.objects.filter(user=self.request.user)

    def get_object(self):
        obj = super().get_object()
        if obj.user_id != self.request.user.id:
            raise PermissionDenied("You can only access your own computed data.")
        return obj


class UserPreferenceViewSet(ModelViewSet):
    """
    CRUD preferences for the authenticated user.
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
