"""Tests for GET /api/security/auth-assurance/ vs banking enforcement rules."""
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.urls import reverse
from rest_framework.test import APIClient

from compliance.models import ConsentType
from compliance.services import record_financial_consent
from users.models import User


class AuthAssuranceViewTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(
            username="assurance_user",
            email="assurance@test.com",
            password="unused",
        )

    @override_settings(BANKING_REQUIRE_MFA=True, BANKING_REQUIRE_FINANCIAL_CONSENT=False)
    @patch("banking.views.create_link_token_for_user")
    def test_blocking_code_matches_link_token_denial(self, mock_create):
        """Auth-assurance MFA outcome matches POST /api/banking/link-token/."""
        mock_create.return_value = "tok"
        self.client.force_authenticate(
            user=self.user,
            token={"aal": "aal1", "mfa_factors_count": 0, "amr": []},
        )
        assurance = self.client.get(reverse("auth-assurance")).json()
        link_res = self.client.post("/api/banking/link-token/")
        self.assertEqual(assurance["blocking_code"], "mfa_not_enrolled")
        self.assertFalse(assurance["banking_allowed"])
        self.assertEqual(link_res.status_code, 403)
        detail = link_res.json().get("detail") or {}
        code = link_res.json().get("code") or detail.get("code")
        self.assertEqual(code, "mfa_not_enrolled")

    @override_settings(BANKING_REQUIRE_MFA=True, BANKING_REQUIRE_FINANCIAL_CONSENT=False)
    def test_aal1_blocked_matches_mfa_not_enrolled(self):
        self.client.force_authenticate(
            user=self.user,
            token={"aal": "aal1", "mfa_factors_count": 0, "amr": []},
        )
        r = self.client.get(reverse("auth-assurance"))
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertFalse(data["banking_allowed"])
        self.assertEqual(data["blocking_code"], "mfa_not_enrolled")
        self.assertEqual(data["aal_normalized"], "aal1")
        self.assertTrue(data["mfa_required_by_policy"])

    @override_settings(BANKING_REQUIRE_MFA=True, BANKING_REQUIRE_FINANCIAL_CONSENT=False)
    def test_aal2_allowed(self):
        self.client.force_authenticate(
            user=self.user,
            token={"aal": "aal2", "mfa_factors_count": -1, "amr": []},
        )
        r = self.client.get(reverse("auth-assurance"))
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertTrue(data["banking_allowed"])
        self.assertIsNone(data["blocking_code"])
        self.assertEqual(data["aal_normalized"], "aal2")

    @override_settings(BANKING_REQUIRE_MFA=False, BANKING_REQUIRE_FINANCIAL_CONSENT=False)
    def test_relaxed_policy_allows_aal1(self):
        self.client.force_authenticate(
            user=self.user,
            token={"aal": "aal1", "mfa_factors_count": 0, "amr": []},
        )
        r = self.client.get(reverse("auth-assurance"))
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertTrue(data["banking_allowed"])
        self.assertIsNone(data["blocking_code"])
        self.assertFalse(data["mfa_required_by_policy"])

    @override_settings(BANKING_REQUIRE_MFA=True, BANKING_REQUIRE_FINANCIAL_CONSENT=True)
    def test_consent_required_blocks_when_not_recorded(self):
        self.client.force_authenticate(
            user=self.user,
            token={"aal": "aal2", "mfa_factors_count": 1, "amr": ["pwd", "otp"]},
        )
        r = self.client.get(reverse("auth-assurance"))
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertFalse(data["banking_allowed"])
        self.assertEqual(data["blocking_code"], "financial_consent_required")
        self.assertFalse(data["financial_consent_valid"])
        self.assertTrue(data["consent_required_by_policy"])

    @override_settings(BANKING_REQUIRE_MFA=True, BANKING_REQUIRE_FINANCIAL_CONSENT=True)
    def test_consent_recorded_allows(self):
        record_financial_consent(
            user=self.user,
            consent_type=ConsentType.FINANCIAL_DATA_ACCESS,
            source="test",
        )
        self.client.force_authenticate(
            user=self.user,
            token={"aal": "aal2", "mfa_factors_count": 1, "amr": ["pwd", "otp"]},
        )
        r = self.client.get(reverse("auth-assurance"))
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertTrue(data["banking_allowed"])
        self.assertIsNone(data["blocking_code"])
        self.assertTrue(data["financial_consent_valid"])
