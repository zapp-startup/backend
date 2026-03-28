from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.viewsets import ModelViewSet, ReadOnlyModelViewSet

from gamification.services import award_points_for_onboarding

from .models import UserRawExplicit, UserRawInferred, UserComputed, UserPreference
from .serializers import (
    UserRawExplicitSerializer,
    UserRawInferredSerializer,
    UserComputedSerializer,
    UserPreferenceSerializer,
)


class UserRawExplicitViewSet(ModelViewSet):
    serializer_class = UserRawExplicitSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return UserRawExplicit.objects.filter(user=self.request.user)

    def perform_create(self, serializer):
        profile = serializer.save(user=self.request.user)
        award_points_for_onboarding(profile.user)

    def perform_update(self, serializer):
        serializer.save(user=self.request.user)


class UserRawInferredViewSet(ReadOnlyModelViewSet):
    serializer_class = UserRawInferredSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return UserRawInferred.objects.filter(user=self.request.user)


class UserComputedViewSet(ReadOnlyModelViewSet):
    serializer_class = UserComputedSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return UserComputed.objects.filter(user=self.request.user)


class UserPreferenceViewSet(ModelViewSet):
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


class SupabaseUserSyncView(APIView):
    """
    Sync the authenticated Supabase session into a backend user profile payload.
    """

    permission_classes = [IsAuthenticated]

    def post(self, request):
        user = request.user
        auth_context = request.auth or {}

        return Response(
            {
                "id": user.id,
                "email": user.email,
                "username": user.username,
                "supabase_uid": str(user.supabase_uid) if user.supabase_uid else None,
            }
        )
