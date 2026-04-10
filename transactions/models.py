from django.conf import settings
from django.db import models
from django.utils import timezone

from zapp.security.encrypted_fields import (
    EncryptedBooleanField,
    EncryptedCharField,
    EncryptedDateTimeField,
    EncryptedDecimalField,
    EncryptedFloatField,
    EncryptedIntegerField,
    EncryptedTextField,
)


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
    bank_transaction = models.OneToOneField(
        "banking.BankTransaction",
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="feedback_transaction",
        help_text="Linked bank-synced transaction when this ledger entry mirrors a synced purchase.",
    )

    direction = models.CharField(
        max_length=16,
        choices=TransactionDirection.choices,
        default=TransactionDirection.SPEND,
    )


    amount = EncryptedDecimalField()
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

    description_raw = EncryptedCharField(
        blank=True,
        null=True,
        help_text="Raw bank/merchant description or user-entered note.",
    )


    satisfaction_rating = EncryptedIntegerField(
        blank=True,
        null=True,
        help_text="1-10 scale: how satisfied with this purchase",
    )
    regret_rating = EncryptedIntegerField(
        blank=True,
        null=True,
        help_text="0-100 scale: level of regret about this purchase",
    )
    repurchase_likelihood = EncryptedIntegerField(
        blank=True,
        null=True,
        help_text="0-100 scale: likelihood to buy again",
    )
    usage_frequency = EncryptedIntegerField(
        blank=True,
        null=True,
        help_text="How often user expects to use this (e.g., times per week)",
    )
    reflection_text = EncryptedTextField(
        blank=True,
        null=True,
        help_text="User's thoughts/notes about this purchase",
    )

 
    considered_at = EncryptedDateTimeField(
        blank=True,
        null=True,
        help_text="When user started considering this purchase (for decision time calculation)",
    )
    used_buy_advisor = models.BooleanField(
        default=False,
        help_text="Did the user use the buy advisor flow for this purchase",
    )
    self_report_researched = EncryptedBooleanField(
        blank=True,
        null=True,
        help_text="Did user report researching before buying (nullable = not asked)",
    )


    impulse_score = EncryptedFloatField(
        blank=True,
        null=True,
        help_text="0-1 computed impulse likelihood for this transaction",
    )
    regret_score = EncryptedFloatField(
        blank=True,
        null=True,
        help_text="0-1 computed regret likelihood for this transaction",
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
    personal_value_score = EncryptedIntegerField(
        blank=True,
        null=True,
        help_text="0-150 persisted model-backed value score for this transaction",
    )
    value_score_confidence = EncryptedFloatField(
        blank=True,
        null=True,
        help_text="0-1 confidence in personal_value_score",
    )
    value_score_model_version = models.CharField(
        max_length=64,
        blank=True,
        help_text="Model version used to produce personal_value_score",
    )
    value_score_computed_at = models.DateTimeField(
        blank=True,
        null=True,
        help_text="When personal_value_score was last computed",
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
    regret_score = EncryptedIntegerField(blank=True, null=True)
    was_worth_it = EncryptedBooleanField(blank=True, null=True)
    notes = EncryptedTextField(blank=True)
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
