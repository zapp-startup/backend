from django.test import TestCase, override_settings
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

from compliance.models import ConsentType
from compliance.services import current_policy_version, record_financial_consent, user_has_valid_financial_consent

User = get_user_model()


class ConsentServiceTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="c1", email="c1@test.com", password="x")

    def test_record_and_validate_consent(self):
        obj, created = record_financial_consent(user=self.user, consent_text="I agree", source="test")
        self.assertTrue(created)
        self.assertIsNotNone(obj)
        self.assertTrue(user_has_valid_financial_consent(self.user))
        self.assertEqual(obj.policy_version, current_policy_version())

    def test_dedupe_within_window(self):
        record_financial_consent(user=self.user, source="test")
        obj2, created2 = record_financial_consent(user=self.user, source="test")
        self.assertFalse(created2)
        self.assertIsNone(obj2)


class PrivacyMetadataTests(TestCase):
    @override_settings(PRIVACY_POLICY_URL="https://example.com/privacy", PRIVACY_POLICY_VERSION="2.0.0")
    def test_privacy_metadata_public(self):
        client = APIClient()
        res = client.get("/api/compliance/privacy-policy/")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data["privacy_policy_url"], "https://example.com/privacy")
        self.assertEqual(data["privacy_policy_version"], "2.0.0")
