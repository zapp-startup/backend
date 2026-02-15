from rest_framework.permissions import IsAuthenticated
from rest_framework.viewsets import ModelViewSet, ReadOnlyModelViewSet
from rest_framework.exceptions import PermissionDenied

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
