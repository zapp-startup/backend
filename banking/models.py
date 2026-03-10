"""
Banking models for Plaid-based bank connections, accounts, and transactions.
Stores raw Plaid data; frontend reads from our DB, not directly from Plaid.
"""
from django.conf import settings
from django.db import models


class BankConnection(models.Model):
    """
    One Plaid Item / one institution login for one user.
    """

    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        DISCONNECTED = "disconnected", "Disconnected"
        ERROR = "error", "Error"

    id = models.BigAutoField(primary_key=True)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="bank_connections",
    )
    plaid_item_id = models.CharField(max_length=64, unique=True, db_index=True)
    plaid_access_token = models.CharField(max_length=512)
    institution_id = models.CharField(max_length=64, blank=True, db_index=True)
    institution_name = models.CharField(max_length=256, blank=True)
    status = models.CharField(
        max_length=32,
        choices=Status.choices,
        default=Status.ACTIVE,
    )
    sync_cursor = models.TextField(blank=True)
    last_synced_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["user", "status"]),
        ]

    def __str__(self) -> str:
        return f"{self.user} • {self.institution_name or self.plaid_item_id}"


class BankAccount(models.Model):
    """
    Accounts under a Plaid connection.
    """

    id = models.BigAutoField(primary_key=True)
    connection = models.ForeignKey(
        BankConnection,
        on_delete=models.CASCADE,
        related_name="accounts",
    )
    plaid_account_id = models.CharField(max_length=64, db_index=True)
    name = models.CharField(max_length=256)
    official_name = models.CharField(max_length=256, blank=True)
    mask = models.CharField(max_length=8, blank=True)
    type = models.CharField(max_length=64, blank=True)
    subtype = models.CharField(max_length=64, blank=True)
    current_balance = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        null=True,
        blank=True,
    )
    available_balance = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        null=True,
        blank=True,
    )
    iso_currency_code = models.CharField(max_length=3, default="USD")
    raw_payload = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(
                fields=["connection", "plaid_account_id"],
                name="uniq_connection_plaid_account",
            ),
        ]
        indexes = [
            models.Index(fields=["connection", "plaid_account_id"]),
        ]

    def __str__(self) -> str:
        return f"{self.name} ({self.mask})"


class BankTransaction(models.Model):
    """
    Synced Plaid transactions. Named BankTransaction to avoid collision
    with transactions.Transaction (user feedback ledger).
    """

    id = models.BigAutoField(primary_key=True)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="bank_transactions",
    )
    connection = models.ForeignKey(
        BankConnection,
        on_delete=models.CASCADE,
        related_name="transactions",
    )
    account = models.ForeignKey(
        BankAccount,
        on_delete=models.CASCADE,
        related_name="transactions",
    )
    plaid_transaction_id = models.CharField(max_length=64, db_index=True)
    pending_transaction_id = models.CharField(max_length=64, blank=True)
    name = models.CharField(max_length=512)
    merchant_name = models.CharField(max_length=512, blank=True)
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    iso_currency_code = models.CharField(max_length=3, default="USD")
    date = models.DateField()
    authorized_date = models.DateField(null=True, blank=True)
    pending = models.BooleanField(default=False)
    removed = models.BooleanField(
        default=False,
        help_text="Soft-delete: transaction was removed in Plaid sync",
    )
    category_primary = models.CharField(max_length=128, blank=True)
    category_detailed = models.CharField(max_length=256, blank=True)
    raw_payload = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-date", "-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["connection", "plaid_transaction_id"],
                name="uniq_connection_plaid_transaction",
            ),
        ]
        indexes = [
            models.Index(fields=["user", "date"]),
            models.Index(fields=["account", "date"]),
            models.Index(fields=["user", "removed", "date"]),
        ]

    def __str__(self) -> str:
        return f"{self.name} {self.amount} {self.date}"
