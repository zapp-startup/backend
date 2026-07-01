"""
Enforce MFA and financial consent before Plaid flows (production policy).
"""
from __future__ import annotations

import logging

from django.conf import settings

from apps.compliance.monitoring import capture_backend_audit_event
from apps.compliance.services import user_has_valid_financial_consent
from apps.users.security_assurance import (
    MfaErrorCode,
    assess_mfa_for_banking,
    extract_assurance_from_auth,
    banking_step_up_fresh,
)

from .exceptions import (
    FinancialConsentRequiredException,
    MfaNotEnrolledException,
    MfaRequiredException,
    MfaVerificationNeededException,
)

logger = logging.getLogger(__name__)


def enforce_banking_policies(request) -> None:
    """
    Raises DRF exception if MFA or consent checks fail.
    Call at the start of Plaid link-token and exchange-token views.
    """
    mfa_policy = getattr(settings, "BANKING_REQUIRE_MFA", False)

    allowed, code, info = assess_mfa_for_banking(request)
    aal_normalized = (info.get("aal") or "").lower() or None
    factors = info.get("mfa_factors_count")

    if allowed and not banking_step_up_fresh(info):
        capture_backend_audit_event(
            event_name="banking.policy_denied",
            outcome="failure",
            actor=request.user,
            action="enforce_policy",
            resource_type="bank_link",
            request=request,
            status_code=403,
            error_code=MfaErrorCode.VERIFICATION_NEEDED,
            error_message="Banking action blocked: step-up is stale or missing.",
            metadata={
                "decision": "blocked",
                "reason_code": "step_up_stale",
                "aal_normalized": aal_normalized,
                "mfa_factors_count": factors,
                "mfa_required_by_policy": mfa_policy,
            },
        )
        logger.warning(
            "banking_policy_check user_id=%s decision=blocked reason_code=step_up_stale",
            getattr(request.user, "pk", None),
        )
        raise MfaVerificationNeededException()

    if allowed:
        reason_code = "ok" if mfa_policy else "mfa_not_required_by_policy"
        logger.info(
            "banking_policy_check user_id=%s mfa_required_by_policy=%s decision=allowed reason_code=%s aal_normalized=%s mfa_factors_count=%s",
            getattr(request.user, "pk", None),
            mfa_policy,
            reason_code,
            aal_normalized,
            factors,
        )
    else:
        capture_backend_audit_event(
            event_name="banking.policy_denied",
            outcome="failure",
            actor=request.user,
            action="enforce_policy",
            resource_type="bank_link",
            request=request,
            status_code=403,
            error_code=code or "unknown",
            error_message="Banking action blocked by MFA policy.",
            metadata={
                "decision": "blocked",
                "reason_code": code or "unknown",
                "aal_normalized": aal_normalized,
                "mfa_factors_count": factors,
                "mfa_required_by_policy": mfa_policy,
            },
        )
        logger.warning(
            "banking_policy_check user_id=%s mfa_required_by_policy=%s decision=blocked reason_code=%s aal_normalized=%s mfa_factors_count=%s",
            getattr(request.user, "pk", None),
            mfa_policy,
            code or "unknown",
            aal_normalized,
            factors,
        )
        if code == MfaErrorCode.NOT_ENROLLED:
            raise MfaNotEnrolledException()
        if code == MfaErrorCode.VERIFICATION_NEEDED:
            raise MfaVerificationNeededException()
        raise MfaRequiredException()

    if getattr(settings, "BANKING_REQUIRE_FINANCIAL_CONSENT", True):
        if not user_has_valid_financial_consent(request.user):
            capture_backend_audit_event(
                event_name="banking.policy_denied",
                outcome="failure",
                actor=request.user,
                action="enforce_policy",
                resource_type="bank_link",
                request=request,
                status_code=428,
                error_code="financial_consent_required",
                error_message="Banking action blocked because consent is missing.",
                metadata={
                    "decision": "blocked",
                    "reason_code": "financial_consent_required",
                    "mfa_required_by_policy": mfa_policy,
                },
            )
            logger.warning(
                "banking_link_token_denied_consent user_id=%s",
                getattr(request.user, "pk", None),
            )
            raise FinancialConsentRequiredException()
