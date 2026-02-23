import uuid
from unittest.mock import patch

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


class SupabaseEmailExtractionTests(APITestCase):
    @patch("users.supabase_auth._fetch_supabase_user_profile")
    def test_extracts_email_from_payload_metadata(self, mock_profile):
        from users.supabase_auth import _extract_email

        mock_profile.return_value = {}
        payload = {"metadata": {"email": "MetaDataUser@Example.com"}}

        email = _extract_email(payload, "token")

        self.assertEqual(email, "metadatauser@example.com")

    @patch("users.supabase_auth._fetch_supabase_user_profile")
    def test_extracts_email_from_profile_metadata(self, mock_profile):
        from users.supabase_auth import _extract_email

        mock_profile.return_value = {"metadata": {"email": "ProfileUser@Example.com"}}
        payload = {}

        email = _extract_email(payload, "token")

        self.assertEqual(email, "profileuser@example.com")
