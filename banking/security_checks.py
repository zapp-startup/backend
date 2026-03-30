"""
Enforce MFA and financial consent before Plaid flows (production policy).
"""
from __future__ import annotations

import logging

from django.conf import settings

from compliance.services import user_has_valid_financial_consent
from users.security_assurance import (
    MfaErrorCode,
    assess_mfa_for_banking,
    extract_assurance_from_auth,
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
    auth = getattr(request, "auth", None)
    mfa_policy = getattr(settings, "BANKING_REQUIRE_MFA", False)

    allowed, code, extra = assess_mfa_for_banking(auth)
    info = extra if extra else extract_assurance_from_auth(auth)
    aal_normalized = (info.get("aal") or "").lower() or None
    factors = info.get("mfa_factors_count")

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
            logger.warning(
                "banking_link_token_denied_consent user_id=%s",
                getattr(request.user, "pk", None),
            )
            raise FinancialConsentRequiredException()
