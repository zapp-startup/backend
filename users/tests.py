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


class SupabaseEmailExtractionTests(APITestCase):
    def test_extracts_email_only_from_top_level_payload_claim(self):
        from users.supabase_auth import _extract_email

        payload = {
            "email": "CanonicalUser@Example.com",
            "metadata": {"email": "MetaDataUser@Example.com"},
            "user_metadata": {"email": "UserMetaDataUser@Example.com"},
        }

        email = _extract_email(payload)

        self.assertEqual(email, "canonicaluser@example.com")

    def test_does_not_extract_email_from_metadata_fallbacks(self):
        from users.supabase_auth import _extract_email

        payload = {
            "metadata": {"email": "MetaDataUser@Example.com"},
            "user_metadata": {"email": "UserMetaDataUser@Example.com"},
        }

        email = _extract_email(payload)

        self.assertIsNone(email)


class SupabaseEmailVerificationTests(APITestCase):
    def test_recognizes_email_confirmed_at_claim(self):
        from users.supabase_auth import _is_email_verified

        self.assertTrue(_is_email_verified({"email_confirmed_at": "2024-01-01T00:00:00Z"}))
        self.assertFalse(_is_email_verified({"email_confirmed_at": None}))
        self.assertFalse(_is_email_verified({}))
