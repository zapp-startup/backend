from django.conf import settings
from django.db import models


class MerchantCategory(models.TextChoices):
    STREAMING = "streaming", "Streaming"
    GROCERY = "grocery", "Grocery"
    FITNESS = "fitness", "Fitness"
    SOFTWARE = "software", "Software"
    UTILITIES = "utilities", "Utilities"
    FOOD = "food", "Food & Delivery"
    EDUCATION = "education", "Education"
    OTHER = "other", "Other"


class BillingCycle(models.TextChoices):
    WEEKLY = "weekly", "Weekly"
    MONTHLY = "monthly", "Monthly"
    YEARLY = "yearly", "Yearly"
    OTHER = "other", "Other"


class SubscriptionStatus(models.TextChoices):
    ACTIVE = "active", "Active"
    PAUSED = "paused", "Paused"
    CANCELED = "canceled", "Canceled"


class Merchant(models.Model):
    """
    Canonical vendor/merchant a user subscribes to.
    Ex: Netflix, Spotify, Costco, Crunchyroll, Planet Fitness.
    """

    id = models.BigAutoField(primary_key=True)
    name = models.CharField(max_length=255, unique=True)
    category = models.CharField(
        max_length=32,
        choices=MerchantCategory.choices,
        default=MerchantCategory.OTHER,
    )
    website_domain = models.CharField(
        max_length=255,
        blank=True,
        null=True,
        help_text="Optional domain like 'netflix.com' used for matching/inference.",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]
        indexes = [
            models.Index(fields=["name"]),
            models.Index(fields=["category"]),
        ]

    def __str__(self) -> str:
        return self.name


class Subscription(models.Model):
    """
    A user's recurring subscription to a given Merchant.
    """

    id = models.BigAutoField(primary_key=True)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="subscriptions",
    )
    merchant = models.ForeignKey(
        Merchant,
        on_delete=models.PROTECT,
        related_name="subscriptions",
        help_text="Merchant is PROTECT to avoid breaking historical records.",
    )

    plan_name = models.CharField(max_length=255, blank=True, null=True)

    status = models.CharField(
        max_length=16,
        choices=SubscriptionStatus.choices,
        default=SubscriptionStatus.ACTIVE,
    )
    billing_cycle = models.CharField(
        max_length=16,
        choices=BillingCycle.choices,
        default=BillingCycle.MONTHLY,
    )

    price = models.DecimalField(max_digits=10, decimal_places=2)
    currency = models.CharField(max_length=3, default="USD")

    started_on = models.DateField(blank=True, null=True)
    renewal_date = models.DateField(blank=True, null=True)
    cancelled_on = models.DateField(blank=True, null=True)

    notes = models.TextField(blank=True, null=True)

    usage_frequency = models.PositiveSmallIntegerField(
        blank=True,
        null=True,
        help_text="Uses per week or your chosen unit",
    )
    reactivation_count = models.PositiveIntegerField(
        default=0,
        help_text="Number of times this subscription has been reactivated",
    )

    feedback_value_score = models.FloatField(
        blank=True,
        null=True,
        help_text="0-1 derived from reflection_text + ratings (user feedback on value)",
    )
    feedback_confidence = models.FloatField(
        blank=True,
        null=True,
        help_text="0-1 confidence in feedback_value_score",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["user", "status"]),
            models.Index(fields=["user", "merchant"]),
            models.Index(fields=["user", "renewal_date"]),
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(feedback_value_score__gte=0, feedback_value_score__lte=1) | models.Q(feedback_value_score__isnull=True),
                name="valid_subscription_feedback_value_score",
            ),
            models.CheckConstraint(
                condition=models.Q(feedback_confidence__gte=0, feedback_confidence__lte=1) | models.Q(feedback_confidence__isnull=True),
                name="valid_subscription_feedback_confidence",
            ),
            # Prevent duplicate active subscriptions to the same merchant for a user.
            # If you later want multiple (e.g., multiple Netflix profiles), loosen this.
            models.UniqueConstraint(
                fields=["user", "merchant"],
                condition=models.Q(status=SubscriptionStatus.ACTIVE),
                name="uniq_active_subscription_per_user_merchant",
            )
        ]

    def __str__(self) -> str:
        base = f"{self.user} • {self.merchant.name}"
        return f"{base} ({self.status})"