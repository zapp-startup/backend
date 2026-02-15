from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.viewsets import ModelViewSet, ReadOnlyModelViewSet

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
        serializer.save(user=self.request.user)

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

    def perform_update(self, serializer):
        serializer.save(user=self.request.user)


class SupabaseUserSyncView(APIView):
    """
    Lightweight endpoint the frontend can call right after Supabase login.

    Authentication is handled by `SupabaseJWTAuthentication` globally; that class
    creates/links a Django user row on first login using the Supabase `sub` UUID.
    """

    permission_classes = [IsAuthenticated]

    def post(self, request):
        user = request.user
        return Response(
            {
                "id": user.id,
                "email": user.email,
                "username": user.username,
                "supabase_uid": str(user.supabase_uid) if user.supabase_uid else None,
            }
        )
