from django.conf import settings
from django.db import models


class ConsentType(models.TextChoices):
    """Types of recorded user consent (extend as needed)."""

    FINANCIAL_DATA_ACCESS = "financial_data_access", "Financial data access (Plaid / bank linking)"


class FinancialConsent(models.Model):
    """
    Immutable consent audit record. Each acceptance creates a new row.
    Deduping identical rapid replays is handled in the service layer, not by
    mutating or deleting rows.
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="financial_consents",
    )
    consent_type = models.CharField(max_length=64, choices=ConsentType.choices, db_index=True)
    policy_version = models.CharField(
        max_length=64,
        db_index=True,
        help_text="Must match configured privacy policy version at acceptance time.",
    )
    consent_text_hash = models.CharField(
        max_length=128,
        blank=True,
        help_text="SHA-256 hex of the exact consent text shown (optional but recommended).",
    )
    source = models.CharField(
        max_length=32,
        default="api",
        help_text="e.g. web, ios, android",
    )
    accepted_at = models.DateTimeField(auto_now_add=True, db_index=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=512, blank=True)
    request_id = models.CharField(max_length=64, blank=True)

    class Meta:
        ordering = ["-accepted_at"]
        indexes = [
            models.Index(fields=["user", "consent_type", "policy_version"]),
            models.Index(fields=["user", "accepted_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.user_id} {self.consent_type} v{self.policy_version} @ {self.accepted_at}"


class AuditEvent(models.Model):
    class Outcome(models.TextChoices):
        ATTEMPT = "attempt", "Attempt"
        SUCCESS = "success", "Success"
        FAILURE = "failure", "Failure"

    class ActorType(models.TextChoices):
        USER = "user", "User"
        ANONYMOUS = "anonymous", "Anonymous"
        SYSTEM = "system", "System"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="audit_events",
    )
    event_name = models.CharField(max_length=128)
    occurred_at = models.DateTimeField(auto_now_add=True, db_index=True)
    outcome = models.CharField(max_length=16, choices=Outcome.choices)
    actor_id = models.CharField(max_length=128, blank=True)
    actor_type = models.CharField(max_length=16, choices=ActorType.choices)
    source_system = models.CharField(max_length=64)
    request_id = models.CharField(max_length=128, blank=True)
    action = models.CharField(max_length=64, blank=True)
    resource_type = models.CharField(max_length=64, blank=True)
    resource_id = models.CharField(max_length=128, blank=True)
    route = models.CharField(max_length=255, blank=True)
    method = models.CharField(max_length=16, blank=True)
    status_code = models.PositiveSmallIntegerField(null=True, blank=True)
    error_code = models.CharField(max_length=64, blank=True)
    error_message = models.TextField(blank=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-occurred_at", "-id"]
        indexes = [
            models.Index(fields=["user", "occurred_at"]),
            models.Index(fields=["resource_type", "resource_id"]),
        ]

    def __str__(self) -> str:
        return f"{self.event_name} ({self.outcome})"
