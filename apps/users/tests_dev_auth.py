from django.test import RequestFactory, TestCase, override_settings

from apps.users.dev_auth import get_dev_user
from apps.users.models import User


class DevAuthTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="dev_u", email="d@test.com", password="x")

    @override_settings(DEBUG=True, ALLOW_DEV_HEADER_AUTH=True)
    def test_dev_header_resolves_user(self):
        factory = RequestFactory()
        req = factory.get("/", HTTP_X_DEV_USER="dev_u")
        self.assertEqual(get_dev_user(req), self.user)

    @override_settings(DEBUG=False, ALLOW_DEV_HEADER_AUTH=True)
    def test_disabled_outside_debug(self):
        factory = RequestFactory()
        req = factory.get("/", HTTP_X_DEV_USER="dev_u")
        self.assertIsNone(get_dev_user(req))
