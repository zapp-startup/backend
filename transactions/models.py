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
    Raw ledger entries. Treat these as "facts", not opinions.
    """

    id = models.BigAutoField(primary_key=True)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="transactions",
    )

    # Merchant + Subscription are optional because not all transactions map cleanly.
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

    # Store amount as a positive number; direction indicates spend vs income/refund.
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

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-occurred_at"]
        indexes = [
            models.Index(fields=["user", "occurred_at"]),
            models.Index(fields=["user", "category", "occurred_at"]),
            models.Index(fields=["subscription", "occurred_at"]),
            models.Index(fields=["merchant", "occurred_at"]),
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
