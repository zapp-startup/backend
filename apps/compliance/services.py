"""
Consent recording and validation. Policy version is authoritative from Django settings.
"""
from __future__ import annotations

import hashlib
import logging
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from .models import ConsentType, FinancialConsent

logger = logging.getLogger(__name__)

# Dedupe window for noisy double-clicks (same user, type, version)
_CONSENT_DEDUPE_SECONDS = 120


def current_policy_version() -> str:
    return getattr(settings, "PRIVACY_POLICY_VERSION", "1.0.0")


def _client_ip(request) -> str | None:
    xff = request.META.get("HTTP_X_FORWARDED_FOR")
    if xff:
        return xff.split(",")[0].strip().strip()
    return request.META.get("REMOTE_ADDR")


def record_financial_consent(
    *,
    user,
    consent_type: str = ConsentType.FINANCIAL_DATA_ACCESS,
    consent_text: str | None = None,
    source: str = "api",
    request=None,
) -> tuple[FinancialConsent | None, bool]:
    """
    Record consent for the current policy version. Returns (record, created).
    If an identical acceptance happened within the dedupe window, returns (None, False).
    """
    pv = current_policy_version()
    text_hash = ""
    if consent_text:
        text_hash = hashlib.sha256(consent_text.encode("utf-8")).hexdigest()

    since = timezone.now() - timedelta(seconds=_CONSENT_DEDUPE_SECONDS)
    if FinancialConsent.objects.filter(
        user=user,
        consent_type=consent_type,
        policy_version=pv,
        consent_text_hash=text_hash,
        accepted_at__gte=since,
    ).exists():
        logger.info(
            "consent_deduped user_id=%s consent_type=%s policy_version=%s",
            user.pk,
            consent_type,
            pv,
        )
        return None, False

    kwargs = {
        "user": user,
        "consent_type": consent_type,
        "policy_version": pv,
        "consent_text_hash": text_hash,
        "source": source,
    }
    if request:
        kwargs["ip_address"] = _client_ip(request) or None
        ua = (request.META.get("HTTP_USER_AGENT") or "")[:512]
        kwargs["user_agent"] = ua
        rid = request.META.get("HTTP_X_REQUEST_ID") or request.META.get("HTTP_X_CORRELATION_ID")
        if rid:
            kwargs["request_id"] = str(rid)[:64]

    with transaction.atomic():
        obj = FinancialConsent.objects.create(**kwargs)

    logger.info(
        "consent_accepted user_id=%s consent_type=%s policy_version=%s source=%s",
        user.pk,
        consent_type,
        pv,
        source,
    )
    return obj, True


def user_has_valid_financial_consent(user) -> bool:
    """True if user accepted the currently configured policy version for financial access."""
    pv = current_policy_version()
    return FinancialConsent.objects.filter(
        user=user,
        consent_type=ConsentType.FINANCIAL_DATA_ACCESS,
        policy_version=pv,
    ).exists()
