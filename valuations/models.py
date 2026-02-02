from django.conf import settings
from django.db import models


class Recommendation(models.TextChoices):
    BUY = "buy", "Buy"
    WAIT = "wait", "Wait"
    SKIP = "skip", "Skip"
    ALTERNATIVE = "alternative", "Alternative"


class ValuationModelVersion(models.Model):
    name = models.CharField(max_length=64)
    version = models.CharField(max_length=32)
    description = models.TextField(blank=True, null=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("name", "version")
        indexes = [
            models.Index(fields=["name", "version"]),
        ]

    def __str__(self) -> str:
        return f"{self.name}@{self.version}"


class SubscriptionValuation(models.Model):
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

    period_start = models.DateField()
    period_end = models.DateField()

    total_cost = models.DecimalField(max_digits=12, decimal_places=2)
    estimated_value = models.DecimalField(max_digits=12, decimal_places=2)
    net_value = models.DecimalField(max_digits=12, decimal_places=2)

    confidence = models.FloatField(default=1.0)
    explanation_json = models.JSONField(default=dict, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-period_end", "-created_at"]
        indexes = [
            models.Index(fields=["user", "period_end"]),
            models.Index(fields=["user", "subscription", "period_end"]),
            models.Index(fields=["subscription", "period_end"]),
        ]

    def __str__(self) -> str:
        return f"{self.user} • {self.subscription} • {self.period_start}→{self.period_end}"


class ItemValuation(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="item_valuations",
    )

    # No Product model yet. We store what the user scanned or typed.
    item_name = models.CharField(max_length=256)
    item_category = models.CharField(max_length=64, blank=True)

    model_version = models.ForeignKey(
        ValuationModelVersion,
        on_delete=models.PROTECT,
        related_name="item_valuations",
    )

    estimated_fair_price = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        blank=True,
        null=True,
    )

    personal_value_score = models.PositiveSmallIntegerField()
    recommendation = models.CharField(
        max_length=16,
        choices=Recommendation.choices,
        default=Recommendation.WAIT,
    )

    reasoning_json = models.JSONField(default=dict, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["user", "created_at"]),
            models.Index(fields=["user", "recommendation", "created_at"]),
            models.Index(fields=["item_name", "created_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.user} • {self.item_name} • {self.recommendation} • {self.personal_value_score}"