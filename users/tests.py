from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase


User = get_user_model()


class SupabaseUserSyncViewTests(APITestCase):
    def test_sync_returns_authenticated_user_profile(self):
        user = User.objects.create_user(
            username="sync-user",
            email="sync@example.com",
            password="unused-password",
        )
        self.client.force_authenticate(
            user=user,
            token={"supabase_uid": "12345678-1234-5678-1234-567812345678"},
        )

        response = self.client.post(reverse("supabase-user-sync"))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            response.json(),
            {
                "id": user.id,
                "email": "sync@example.com",
                "username": "sync-user",
                "supabase_uid": "12345678-1234-5678-1234-567812345678",
            },
        )
