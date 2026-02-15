import uuid

from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase


User = get_user_model()


class SupabaseUserSyncViewTests(APITestCase):
    def test_sync_returns_authenticated_user_payload(self):
        user = User.objects.create_user(
            username="jane@example.com",
            email="jane@example.com",
            supabase_uid=uuid.uuid4(),
            password="testpass123",
        )
        self.client.force_authenticate(user=user)

        response = self.client.post(reverse("supabase-user-sync"))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["id"], user.id)
        self.assertEqual(response.data["email"], user.email)
        self.assertEqual(response.data["username"], user.username)
        self.assertEqual(response.data["supabase_uid"], str(user.supabase_uid))

    def test_sync_requires_authentication(self):
        response = self.client.post(reverse("supabase-user-sync"))
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
