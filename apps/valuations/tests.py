from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework.test import APIClient
from rest_framework import status
from django.test import TestCase

User = get_user_model()


class ValuationModelVersionPermissionTests(TestCase):
    """
    ValuationModelVersion is operator-owned metadata. It must never be readable
    or writable by anonymous or ordinary authenticated users -- only staff.

    Regression guard for the previously world-open viewset (it declared no
    permission_classes and relied on DRF's implicit AllowAny default).
    """

    def setUp(self):
        self.url = reverse("valuation-model-versions-list")
        self.user = User.objects.create_user(username="member", password="pw")
        self.staff = User.objects.create_user(
            username="operator", password="pw", is_staff=True
        )

    def test_anonymous_is_denied(self):
        resp = APIClient().get(self.url)
        self.assertIn(
            resp.status_code,
            (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN),
        )

    def test_ordinary_user_is_denied(self):
        client = APIClient()
        client.force_authenticate(self.user)
        self.assertEqual(client.get(self.url).status_code, status.HTTP_403_FORBIDDEN)

    def test_ordinary_user_cannot_create(self):
        client = APIClient()
        client.force_authenticate(self.user)
        resp = client.post(self.url, {}, format="json")
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_staff_user_is_allowed(self):
        client = APIClient()
        client.force_authenticate(self.staff)
        self.assertEqual(client.get(self.url).status_code, status.HTTP_200_OK)


class DefaultPermissionFailClosedTests(TestCase):
    """
    The project-wide DEFAULT_PERMISSION_CLASSES must be fail-closed. Any view
    that forgets to declare permission_classes should require authentication
    rather than fall back to DRF's implicit AllowAny.
    """

    def test_default_permission_is_is_authenticated(self):
        from django.conf import settings

        defaults = settings.REST_FRAMEWORK.get("DEFAULT_PERMISSION_CLASSES", ())
        self.assertIn(
            "rest_framework.permissions.IsAuthenticated",
            tuple(defaults),
            "DEFAULT_PERMISSION_CLASSES must fail closed with IsAuthenticated.",
        )
