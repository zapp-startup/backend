from django.conf import settings
from django.db import models
from django.utils import timezone


class TransactionDirection(models.TextChoices):
    SPEND = "spend", "Spend"
    INCOME = "income", "Income"
    REFUND = "refund", "Refund"


class TransactionCategory(models.TextChoices):
    SUBSCRIPTIONS = "subscriptions", "Subscriptions"
    GROCERIES = "groceries", "Groceries"
    EATING_OUT = "eating_out", "Eating Out"
    TRANSPORT = "transport", "Transport"
    SHOPPING = "shopping", "Shopping"
    BILLS = "bills", "Bills"
    ENTERTAINMENT = "entertainment", "Entertainment"
    HEALTH = "health", "Health"
    EDUCATION = "education", "Education"
    OTHER = "other", "Other"


class PaymentChannel(models.TextChoices):
    CARD = "card", "Card"
    CASH = "cash", "Cash"
    ONLINE = "online", "Online"
    BANK = "bank", "Bank Transfer"
    OTHER = "other", "Other"


class Transaction(models.Model):
    """
    Raw ledger entries + optional per-transaction feedback and timing context.
    These are "facts" that feed into behavioral inference.
    """

    id = models.BigAutoField(primary_key=True)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="transactions",
    )


    merchant = models.ForeignKey(
        "subscriptions.Merchant",
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="transactions",
    )
    subscription = models.ForeignKey(
        "subscriptions.Subscription",
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="transactions",
        help_text="Link if this transaction is a known subscription charge.",
    )

    direction = models.CharField(
        max_length=16,
        choices=TransactionDirection.choices,
        default=TransactionDirection.SPEND,
    )


    amount = models.DecimalField(max_digits=12, decimal_places=2)
    currency = models.CharField(max_length=3, default="USD")

    occurred_at = models.DateTimeField()

    category = models.CharField(
        max_length=32,
        choices=TransactionCategory.choices,
        default=TransactionCategory.OTHER,
    )
    payment_channel = models.CharField(
        max_length=16,
        choices=PaymentChannel.choices,
        default=PaymentChannel.CARD,
    )

    description_raw = models.CharField(
        max_length=512,
        blank=True,
        null=True,
        help_text="Raw bank/merchant description or user-entered note.",
    )


    satisfaction_rating = models.PositiveSmallIntegerField(
        blank=True,
        null=True,
        help_text="1-10 scale: how satisfied with this purchase",
    )
    regret_rating = models.PositiveSmallIntegerField(
        blank=True,
        null=True,
        help_text="0-100 scale: level of regret about this purchase",
    )
    repurchase_likelihood = models.PositiveSmallIntegerField(
        blank=True,
        null=True,
        help_text="0-100 scale: likelihood to buy again",
    )
    usage_frequency = models.PositiveSmallIntegerField(
        blank=True,
        null=True,
        help_text="How often user expects to use this (e.g., times per week)",
    )
    reflection_text = models.TextField(
        blank=True,
        null=True,
        help_text="User's thoughts/notes about this purchase",
    )

 
    considered_at = models.DateTimeField(
        blank=True,
        null=True,
        help_text="When user started considering this purchase (for decision time calculation)",
    )
    used_buy_advisor = models.BooleanField(
        default=False,
        help_text="Did the user use the buy advisor flow for this purchase",
    )
    self_report_researched = models.BooleanField(
        blank=True,
        null=True,
        help_text="Did user report researching before buying (nullable = not asked)",
    )


    impulse_score = models.FloatField(
        blank=True,
        null=True,
        help_text="0-1 computed impulse likelihood for this transaction",
    )
    regret_score = models.FloatField(
        blank=True,
        null=True,
        help_text="0-1 computed regret likelihood for this transaction",
    )

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-occurred_at"]
        indexes = [
            models.Index(fields=["user", "occurred_at"]),
            models.Index(fields=["user", "category", "occurred_at"]),
            models.Index(fields=["subscription", "occurred_at"]),
            models.Index(fields=["merchant", "occurred_at"]),

            models.Index(fields=["user", "regret_rating"]),
            models.Index(fields=["user", "satisfaction_rating"]),
        ]
        constraints = [

            models.CheckConstraint(
                condition=models.Q(satisfaction_rating__gte=1, satisfaction_rating__lte=10) | models.Q(satisfaction_rating__isnull=True),
                name="valid_satisfaction_rating",
            ),
            models.CheckConstraint(
                condition=models.Q(regret_rating__gte=0, regret_rating__lte=100) | models.Q(regret_rating__isnull=True),
                name="valid_regret_rating",
            ),
            models.CheckConstraint(
                condition=models.Q(repurchase_likelihood__gte=0, repurchase_likelihood__lte=100) | models.Q(repurchase_likelihood__isnull=True),
                name="valid_repurchase_likelihood",
            ),
            models.CheckConstraint(
                condition=models.Q(impulse_score__gte=0, impulse_score__lte=1) | models.Q(impulse_score__isnull=True),
                name="valid_impulse_score",
            ),
            models.CheckConstraint(
                condition=models.Q(regret_score__gte=0, regret_score__lte=1) | models.Q(regret_score__isnull=True),
                name="valid_regret_score",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.user} • {self.direction} {self.amount} {self.currency} @ {self.occurred_at}"

class TransactionReflection(models.Model):
    id = models.BigAutoField(primary_key=True)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="transaction_reflections",
    )
    transaction = models.ForeignKey(
        Transaction,
        on_delete=models.CASCADE,
        related_name="reflections",
    )
    reflected_at = models.DateTimeField(default=timezone.now)
    regret_score = models.PositiveSmallIntegerField(blank=True, null=True)
    was_worth_it = models.BooleanField(blank=True, null=True)
    notes = models.TextField(blank=True)
    reflected_same_day = models.BooleanField(default=False)

    class Meta:
        ordering = ["-reflected_at"]
        constraints = [
            models.UniqueConstraint(fields=["user", "transaction"], name="uniq_user_transaction_reflection"),
        ]
        indexes = [
            models.Index(fields=["user", "reflected_at"]),
            models.Index(fields=["transaction", "reflected_at"]),
            models.Index(fields=["user", "reflected_same_day"]),
        ]

    def save(self, *args, **kwargs):
        if self.transaction_id and self.reflected_at:
            self.reflected_same_day = self.transaction.occurred_at.date() == self.reflected_at.date()
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return f"{self.user} reflection for txn {self.transaction_id}"
