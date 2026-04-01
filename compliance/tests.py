from django.test import TestCase, override_settings
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

from compliance.monitoring import evaluate_security_alerts, load_security_detection_rules
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
        self.assertEqual(event.metadata["field"], "amount")
        self.assertIn("source_ip", event.metadata)

    def test_audit_ingest_redacts_sensitive_payloads(self):
        self.client.force_authenticate(user=self.user)
        response = self.client.post(
            "/api/audit/events/",
            {
                "event_name": "auth.login",
                "outcome": "failure",
                "source_system": "frontend-web",
                "error_code": "invalid_credentials",
                "error_message": '{"access_token":"secret-token","detail":"bad"}',
                "metadata": {"cookie_header": "csrftoken=abc", "nested": {"refresh_token": "secret"}},
            },
            format="json",
            HTTP_X_FORWARDED_FOR="203.0.113.10",
            HTTP_USER_AGENT="security-test-agent",
        )

        self.assertEqual(response.status_code, 201)
        event = AuditEvent.objects.get(event_name="auth.login")
        self.assertEqual(event.user, self.user)
        self.assertEqual(event.actor_type, AuditEvent.ActorType.USER)
        self.assertEqual(event.error_code, "invalid_credentials")
        self.assertEqual(event.metadata["source_ip"], "203.0.113.10")
        self.assertEqual(event.metadata["user_agent"], "security-test-agent")
        self.assertEqual(event.metadata["cookie_header"], "[REDACTED]")
        self.assertEqual(event.metadata["nested"]["refresh_token"], "[REDACTED]")
        self.assertIn("[REDACTED]", event.error_message)

    def test_audit_ingest_requires_authentication(self):
        response = self.client.post(
            "/api/audit/events/",
            {
                "event_name": "auth.login",
                "outcome": "failure",
                "source_system": "frontend-web",
            },
            format="json",
        )
        self.assertIn(response.status_code, (401, 403))


class SecurityAlertEvaluationTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="detector", email="detector@test.com", password="x")

    def test_rule_config_loads_expected_rules(self):
        rules = load_security_detection_rules()["rules"]
        self.assertEqual({rule["id"] for rule in rules}, {
            "auth_failed_login_spike",
            "privileged_action_event",
            "banking_policy_denied_spike",
        })

    def test_failed_login_spike_alerts_with_triage_context(self):
        for _ in range(5):
            AuditEvent.objects.create(
                event_name="auth.login",
                outcome="failure",
                actor_type=AuditEvent.ActorType.ANONYMOUS,
                actor_id="",
                source_system="frontend-web",
                metadata={"source_ip": "203.0.113.10", "email_domain": "example.com"},
            )
        alert = next(item for item in evaluate_security_alerts() if item["rule_id"] == "auth_failed_login_spike")
        self.assertEqual(alert["severity"], "medium")
        self.assertEqual(alert["routing_target"], "security-triage")
        self.assertEqual(alert["triage"]["group_key"], "203.0.113.10")

    def test_privileged_action_emits_high_alert(self):
        AuditEvent.objects.create(
            user=self.user,
            event_name="rbac.group_member_role_change",
            outcome="success",
            actor_type=AuditEvent.ActorType.USER,
            actor_id=str(self.user.pk),
            source_system="frontend-web",
            request_id="req-1",
            route="/api/gamification/groups/1/update_member_role/",
        )
        alert = next(item for item in evaluate_security_alerts() if item["rule_id"] == "privileged_action_event")
        self.assertEqual(alert["severity"], "high")
        self.assertEqual(alert["triage"]["request_ids"], ["req-1"])
