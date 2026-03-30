"""
Backend-authoritative MFA / assurance assessment using Supabase session claims.

Assumptions (documented; verify against your Supabase project):
- Supabase access tokens include an `aal` claim: "aal1" (single-factor) or
  "aal2" (multi-factor satisfied for this session) when MFA is enabled.
- Optional `amr` (Authentication Methods References) may list methods used.
- The `/auth/v1/user` response may include a `factors` list when MFA is enrolled.
  We merge factor counts when that response was loaded during authentication.

This module does not replace Supabase-side MFA configuration; it enforces
that the API only allows sensitive banking actions when the presented JWT
indicates aal2 (or policy allows dev bypass).
"""
from __future__ import annotations

from typing import Any

from django.conf import settings

from compliance.services import user_has_valid_financial_consent

# JWT / Supabase user claim keys
AAL_CLAIM = "aal"
AMR_CLAIM = "amr"
FACTORS_KEY = "factors"


class MfaErrorCode:
    NOT_ENROLLED = "mfa_not_enrolled"
    VERIFICATION_NEEDED = "mfa_verification_needed"
    REQUIRED = "mfa_required"


def extract_assurance_from_auth(auth: Any) -> dict:
    """
    Normalize auth context dict from SupabaseJWTAuthentication.
    Returns keys: aal, amr, mfa_factors_count (-1 if unknown).
    """
    if not isinstance(auth, dict):
        return {"aal": None, "amr": [], "mfa_factors_count": -1}
    amr = auth.get(AMR_CLAIM)
    if not isinstance(amr, list):
        amr = []
    count = auth.get("mfa_factors_count")
    if count is None:
        count = -1
    return {
        "aal": auth.get(AAL_CLAIM),
        "amr": amr,
        "mfa_factors_count": int(count) if count is not None else -1,
    }


def assess_mfa_for_banking(auth: Any) -> tuple[bool, str | None, dict]:
    """
    Returns (allowed, error_code, extra).

    When BANKING_REQUIRE_MFA is False, always allows (bypass).
    When True: only aal2 passes; aal1 / missing aal fails; unknown factor count fails
    conservatively (mfa_required) unless aal2.
    """
    if not getattr(settings, "BANKING_REQUIRE_MFA", False):
        return True, None, {}

    info = extract_assurance_from_auth(auth)
    aal = (info["aal"] or "").lower()
    factors = info["mfa_factors_count"]

    if aal == "aal2":
        return True, None, info

    # aal1 or missing: distinguish enrollment when factor count is known
    if factors == 0:
        return False, MfaErrorCode.NOT_ENROLLED, info
    if factors > 0:
        return False, MfaErrorCode.VERIFICATION_NEEDED, info

    # Unknown factors: conservative — require step-up (frontend should re-verify MFA)
    return False, MfaErrorCode.REQUIRED, info


def build_assurance_payload(request) -> dict:
    """
    Payload for GET /api/security/auth-assurance/.
    Uses the same MFA + consent rules as enforce_banking_policies (banking flows).
    """
    auth = getattr(request, "auth", None)
    info = extract_assurance_from_auth(auth)
    allowed, code, _ = assess_mfa_for_banking(auth)
    consent_required = getattr(settings, "BANKING_REQUIRE_FINANCIAL_CONSENT", True)
    consent_ok = (
        user_has_valid_financial_consent(request.user) if consent_required else True
    )
    banking_allowed = allowed and consent_ok
    blocking_code = None
    if not allowed:
        blocking_code = code
    elif consent_required and not consent_ok:
        blocking_code = "financial_consent_required"

    aal = info.get("aal")
    aal_normalized = (aal or "").lower() or None

    return {
        "mfa_required_by_policy": getattr(settings, "BANKING_REQUIRE_MFA", False),
        "consent_required_by_policy": consent_required,
        "financial_consent_valid": consent_ok,
        "assurance": info,
        "aal_normalized": aal_normalized,
        "banking_allowed": banking_allowed,
        "blocking_code": blocking_code,
    }
