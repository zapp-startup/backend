"""
Backend-authoritative MFA / assurance assessment.

Primary path: Django session state written at login / MFA verify (BFF).
Compatibility: SupabaseJWTAuthentication still supplies a dict on request.auth.
"""
from __future__ import annotations

import time
from typing import Any

from django.conf import settings

from apps.compliance.services import user_has_valid_financial_consent

from .session_auth import get_session_auth_state

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


def extract_assurance_from_request(request) -> dict:
    """
    Prefer server session auth; fall back to JWT auth context (legacy clients).
    Includes _assurance_source: 'session' | 'jwt' | 'none'.
    """
    block = get_session_auth_state(request)
    if isinstance(block, dict) and block.get("user_id"):
        count = block.get("mfa_factor_count")
        if count is None:
            count = -1
        return {
            "aal": block.get("aal"),
            "amr": [],
            "mfa_factors_count": int(count) if count is not None else -1,
            "last_step_up_at": block.get("last_step_up_at"),
            "_assurance_source": "session",
        }
    auth = getattr(request, "auth", None)
    info = extract_assurance_from_auth(auth)
    info["last_step_up_at"] = None
    if isinstance(auth, dict) and auth.get("last_step_up_at") is not None:
        info["last_step_up_at"] = auth.get("last_step_up_at")
    info["_assurance_source"] = "jwt" if isinstance(auth, dict) else "none"
    return info


def assess_mfa_for_banking(request) -> tuple[bool, str | None, dict]:
    """
    Returns (allowed, error_code, extra).

    When BANKING_REQUIRE_MFA is False, always allows (bypass).
    When True: only aal2 passes; aal1 / missing aal fails; unknown factor count fails
    conservatively (mfa_required) unless aal2.
    """
    info = extract_assurance_from_request(request)
    if not getattr(settings, "BANKING_REQUIRE_MFA", False):
        return True, None, info

    aal = (info.get("aal") or "").lower()
    factors = info["mfa_factors_count"]

    if aal == "aal2":
        return True, None, info

    if factors == 0:
        return False, MfaErrorCode.NOT_ENROLLED, info
    if factors > 0:
        return False, MfaErrorCode.VERIFICATION_NEEDED, info

    return False, MfaErrorCode.REQUIRED, info


def build_assurance_payload(request) -> dict:
    """
    Payload for GET /api/auth/assurance/ and legacy GET /api/security/auth-assurance/.
    Uses the same MFA + consent rules as enforce_banking_policies (banking flows).
    """
    allowed, code, info = assess_mfa_for_banking(request)
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

    assurance_public = {
        k: v
        for k, v in info.items()
        if not str(k).startswith("_")
    }

    return {
        "mfa_required_by_policy": getattr(settings, "BANKING_REQUIRE_MFA", False),
        "consent_required_by_policy": consent_required,
        "financial_consent_valid": consent_ok,
        "assurance": assurance_public,
        "aal_normalized": aal_normalized,
        "banking_allowed": banking_allowed,
        "blocking_code": blocking_code,
    }


def banking_step_up_fresh(info: dict) -> bool:
    """True if session-bound aal2 has a recent last_step_up_at (or policy off)."""
    if not getattr(settings, "BANKING_STEP_UP_REQUIRED", False):
        return True
    if info.get("_assurance_source") != "session":
        return True
    aal = (info.get("aal") or "").lower()
    if aal != "aal2":
        return True
    last = info.get("last_step_up_at")
    if last is None:
        return False
    try:
        last_i = int(last)
    except (TypeError, ValueError):
        return False
    window = int(getattr(settings, "BANKING_STEP_UP_FRESHNESS_SECONDS", 900))
    return (int(time.time()) - last_i) <= window
