from django.test import TestCase, override_settings
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

from compliance.models import AuditEvent, ConsentType
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


class AuditEventIngestTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(
            username="audit-user",
            email="audit@test.com",
            password="x",
        )

    def test_audit_ingest_persists_authenticated_event_and_overrides_actor_fields(self):
        self.client.force_authenticate(user=self.user)

        response = self.client.post(
            "/api/audit/events/",
            {
                "event_name": "transaction.update",
                "occurred_at": "2026-03-31T22:00:00Z",
                "outcome": "success",
                "actor_id": "forged-client-value",
                "actor_type": "system",
                "source_system": "frontend-web",
                "request_id": "req_123",
                "action": "update",
                "resource_type": "transaction",
                "resource_id": "17",
                "route": "/api/transactions/17/",
                "method": "patch",
                "status_code": 200,
                "metadata": {"field": "amount"},
            },
            format="json",
        )

        self.assertEqual(response.status_code, 201)
        event = AuditEvent.objects.get()
        self.assertEqual(event.user, self.user)
        self.assertEqual(event.actor_type, AuditEvent.ActorType.USER)
        self.assertNotEqual(event.actor_id, "forged-client-value")
        self.assertEqual(event.event_name, "transaction.update")
        self.assertEqual(event.method, "PATCH")
        self.assertEqual(event.route, "/api/transactions/17/")
        self.assertEqual(event.metadata, {"field": "amount"})

    def test_audit_ingest_accepts_anonymous_event(self):
        response = self.client.post(
            "/api/audit/events/",
            {
                "event_name": "auth.login.failed",
                "outcome": "failure",
                "source_system": "frontend-web",
                "error_code": "invalid_credentials",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 201)
        event = AuditEvent.objects.get(event_name="auth.login.failed")
        self.assertIsNone(event.user)
        self.assertEqual(event.actor_type, AuditEvent.ActorType.ANONYMOUS)
        self.assertEqual(event.error_code, "invalid_credentials")
