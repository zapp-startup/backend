from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models

from core.security.encrypted_fields import (
    EncryptedCharField,
    EncryptedDateField,
    EncryptedDecimalField,
    EncryptedFloatField,
    EncryptedIntegerField,
    EncryptedTextField,
)


class SubscriptionEligibility(models.TextChoices):
    NOT_SUBSCRIBABLE = "not_subscribable", "Not Subscribable"
    MEMBERSHIP = "membership", "Membership"
    STANDARD_SUBSCRIPTION = "standard_subscription", "Standard Subscription"
    UTILITY_RECURRING = "utility_recurring", "Utility Recurring"
    INSURANCE_RECURRING = "insurance_recurring", "Insurance Recurring"


class MerchantCategory(models.TextChoices):
    STREAMING = "streaming", "Streaming"
    GROCERY = "grocery", "Grocery"
    FITNESS = "fitness", "Fitness"
    SOFTWARE = "software", "Software"
    UTILITIES = "utilities", "Utilities"
    FOOD = "food", "Food & Delivery"
    EDUCATION = "education", "Education"
    # Health/pharmacy category. Note: not in the trained ML model's merchant-category
    # vocabulary (MERCHANT_CATEGORY_VOCAB). At the model input boundary this is mapped to
    # "other" to preserve backward compatibility. Use this value everywhere else in app logic.
    HEALTH = "health", "Health & Pharmacy"
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
    subscription_eligibility = models.CharField(
        max_length=32,
        choices=SubscriptionEligibility.choices,
        default=SubscriptionEligibility.NOT_SUBSCRIBABLE,
        help_text="Whether this merchant can appear as a recurring subscription in synthetic data.",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]
        indexes = [
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

    plan_name = EncryptedCharField(blank=True, null=True)

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

    price = EncryptedDecimalField()
    currency = models.CharField(max_length=3, default="USD")

    started_on = EncryptedDateField(blank=True, null=True)
    renewal_date = EncryptedDateField(blank=True, null=True)
    cancelled_on = EncryptedDateField(blank=True, null=True)

    notes = EncryptedTextField(blank=True, null=True)

    usage_frequency = EncryptedFloatField(
        blank=True,
        null=True,
        help_text="0-1 normalized usage intensity (e.g. Spotify: active listening days / 30).",
        validators=[MinValueValidator(0.0), MaxValueValidator(1.0)],
    )
    reactivation_count = EncryptedIntegerField(
        default=0,
        help_text="Number of times this subscription has been reactivated",
    )

    feedback_value_score = EncryptedFloatField(
        blank=True,
        null=True,
        help_text="0-1 derived from reflection_text + ratings (user feedback on value)",
    )
    feedback_confidence = EncryptedFloatField(
        blank=True,
        null=True,
        help_text="0-1 confidence in feedback_value_score",
    )
    subscription_utilization = EncryptedFloatField(
        blank=True,
        null=True,
        help_text="0-1 normalized utilization score derived from valuation runs.",
    )
    subscription_cost_benefit = EncryptedFloatField(
        blank=True,
        null=True,
        help_text="0-1 bounded value-vs-cost score derived from valuation runs.",
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
