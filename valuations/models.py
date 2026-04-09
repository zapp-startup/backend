from django.conf import settings
from django.db import models

from zapp.security.encrypted_fields import EncryptedCharField, EncryptedDecimalField, EncryptedJSONField


class Recommendation(models.TextChoices):
    BUY = "buy", "Buy"
    WAIT = "wait", "Wait"
    SKIP = "skip", "Skip"
    ALTERNATIVE = "alternative", "Alternative"


class ValuationContext(models.TextChoices):
    """Context in which a valuation was performed"""
    SUBSCRIPTION_RENEWAL = "subscription_renewal", "Subscription Renewal"
    SUBSCRIPTION_CANCEL = "subscription_cancel", "Subscription Cancel"
    ONE_OFF_PURCHASE = "one_off_purchase", "One-off Purchase"
    UPGRADE = "upgrade", "Upgrade"
    OTHER = "other", "Other"


class ValuationModelVersion(models.Model):
    """
    Model audit + version control for valuation outputs.
    Allows tracking which model/algorithm version produced each valuation.
    """
    id = models.BigAutoField(primary_key=True)
    name = models.CharField(max_length=64)
    version = models.CharField(max_length=32)
    description = models.TextField(
        blank=True,
        null=True,
        help_text="What changed in this version",
    )

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("name", "version")
        indexes = [
            models.Index(fields=["name", "version"]),
        ]

    def __str__(self) -> str:
        return f"{self.name}@{self.version}"


class SubscriptionValuation(models.Model):
    """
    Periodic subscription evaluation for a time window.
    Includes cost/value/net analysis + recommendation with evidence snapshot and explanation.
    """
    id = models.BigAutoField(primary_key=True)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="subscription_valuations",
    )

    subscription = models.ForeignKey(
        "subscriptions.Subscription",
        on_delete=models.CASCADE,
        related_name="valuations",
    )

    model_version = models.ForeignKey(
        ValuationModelVersion,
        on_delete=models.PROTECT,
        related_name="subscription_valuations",
    )

    context = models.CharField(
        max_length=32,
        choices=ValuationContext.choices,
        default=ValuationContext.SUBSCRIPTION_RENEWAL,
        help_text="What triggered this valuation",
    )

    period_start = models.DateField()
    period_end = models.DateField()

    total_cost = EncryptedDecimalField()
    estimated_value = EncryptedDecimalField()
    net_value = EncryptedDecimalField()

    personal_value_score = models.PositiveSmallIntegerField(
        blank=True,
        null=True,
        help_text="0-150 personalized fit score (match ~100, underused <80, extremely useful >100)",
    )

    recommendation = models.CharField(
        max_length=16,
        choices=Recommendation.choices,
        blank=True,
        help_text="Buy/Wait/Skip/Alternative recommendation",
    )

    confidence = models.FloatField(
        default=1.0,
        help_text="0-1 confidence score for this valuation",
    )

    evidence_json = EncryptedJSONField(
        default=dict,
        blank=True,
        help_text="Snapshot of inputs used: weights, raw data, computed metrics",
    )

    explanation_json = EncryptedJSONField(
        default=dict,
        blank=True,
        help_text="UI-friendly explanation: summary, drivers, pros/cons",
    )

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-period_end", "-created_at"]
        indexes = [
            models.Index(fields=["user", "period_end"]),
            models.Index(fields=["user", "subscription", "period_end"]),
            models.Index(fields=["subscription", "period_end"]),
            models.Index(fields=["recommendation"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["user", "subscription", "period_start", "period_end", "model_version"],
                name="uniq_subscription_valuation",
            ),
            models.CheckConstraint(
                condition=models.Q(confidence__gte=0, confidence__lte=1),
                name="valid_subscription_confidence",
            ),
            models.CheckConstraint(
                condition=models.Q(personal_value_score__gte=0, personal_value_score__lte=150) | models.Q(personal_value_score__isnull=True),
                name="valid_subscription_personal_value_score",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.user} • {self.subscription} • {self.period_start}→{self.period_end} • {self.recommendation}"


class ItemValuation(models.Model):
    """
    One-off purchase recommendation + personal score.
    Includes evidence snapshot and reasoning for transparency.
    """
    id = models.BigAutoField(primary_key=True)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="item_valuations",
    )


    item_name = EncryptedCharField(max_length=256)
    item_category = EncryptedCharField(max_length=64, blank=True)

    model_version = models.ForeignKey(
        ValuationModelVersion,
        on_delete=models.PROTECT,
        related_name="item_valuations",
    )


    context = models.CharField(
        max_length=32,
        choices=ValuationContext.choices,
        default=ValuationContext.ONE_OFF_PURCHASE,
        help_text="What triggered this valuation",
    )


    observed_price = EncryptedDecimalField(
        blank=True,
        null=True,
        help_text="Price the user is considering paying",
    )

    estimated_fair_price = EncryptedDecimalField(
        blank=True,
        null=True,
        help_text="Market-based fair price estimate",
    )

    personal_value_score = models.PositiveSmallIntegerField(
        help_text="0-150 personalized fit score (match ~100, underused <80, extremely useful >100)",
    )


    recommendation = models.CharField(
        max_length=16,
        choices=Recommendation.choices,
        default=Recommendation.WAIT,
    )

    confidence = models.FloatField(
        default=1.0,
        help_text="0-1 confidence score for this valuation",
    )

    evidence_json = EncryptedJSONField(
        default=dict,
        blank=True,
        help_text="Snapshot of inputs: computed weights, raw explicit/inferred data",
    )

    reasoning_json = EncryptedJSONField(
        default=dict,
        blank=True,
        help_text="UI-friendly reasoning: pros, cons, alternatives",
    )

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["user", "created_at"]),
            models.Index(fields=["user", "recommendation", "created_at"]),
            models.Index(fields=["item_name", "created_at"]),
            models.Index(fields=["recommendation"]),
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(personal_value_score__gte=0, personal_value_score__lte=150),
                name="valid_personal_value_score",
            ),
            models.CheckConstraint(
                condition=models.Q(confidence__gte=0, confidence__lte=1),
                name="valid_item_confidence",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.user} • {self.item_name} • {self.recommendation} • {self.personal_value_score}/100"
