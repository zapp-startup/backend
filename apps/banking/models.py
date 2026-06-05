"""
Banking models for Plaid-based bank connections, accounts, and transactions.
Stores raw Plaid data; frontend reads from our DB, not directly from Plaid.

Zapp categorization: BankTransaction has zapp_primary_category, zapp_subcategory,
user_override_category. Plaid raw fields (category_primary, category_detailed) are preserved.
"""
from django.conf import settings
from django.db import models

from apps.banking.categories import ZappPrimaryCategory, ZappSubcategory
from core.security.encrypted_fields import (
    EncryptedCharField,
    EncryptedDateField,
    EncryptedDecimalField,
    EncryptedJSONField,
)


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
    # Encrypted-at-app-layer tokens may exceed 512 chars; never expose via API.
    plaid_access_token = models.TextField()
    institution_id = models.CharField(max_length=64, blank=True, db_index=True)
    institution_name = EncryptedCharField(blank=True)
    status = models.CharField(
        max_length=32,
        choices=Status.choices,
        default=Status.ACTIVE,
    )
    sync_cursor = EncryptedCharField(blank=True)
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
    name = EncryptedCharField()
    official_name = EncryptedCharField(blank=True)
    mask = EncryptedCharField(blank=True)
    type = models.CharField(max_length=64, blank=True)
    subtype = models.CharField(max_length=64, blank=True)
    current_balance = EncryptedDecimalField(null=True, blank=True)
    available_balance = EncryptedDecimalField(null=True, blank=True)
    iso_currency_code = models.CharField(max_length=3, default="USD")
    raw_payload = EncryptedJSONField(default=dict, blank=True)
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
    name = EncryptedCharField()
    merchant_name = EncryptedCharField(blank=True)
    amount = EncryptedDecimalField()
    iso_currency_code = models.CharField(max_length=3, default="USD")
    date = models.DateField()
    authorized_date = EncryptedDateField(null=True, blank=True)
    pending = models.BooleanField(default=False)
    removed = models.BooleanField(
        default=False,
        help_text="Soft-delete: transaction was removed in Plaid sync",
    )
    # --- Plaid raw category (preserved, never overwritten by Zapp) ---
    category_primary = EncryptedCharField(blank=True)
    category_detailed = EncryptedCharField(blank=True)

    # --- Zapp categorization layer ---
    zapp_primary_category = models.CharField(
        max_length=64,
        choices=ZappPrimaryCategory.choices,
        blank=True,
        db_index=True,
    )
    zapp_subcategory = EncryptedCharField(
        choices=ZappSubcategory.choices,
        blank=True,
    )
    user_override_category = EncryptedCharField(
        blank=True,
        help_text="User-assigned category; takes precedence over Zapp categories",
    )
    category_source = EncryptedCharField(
        blank=True,
        help_text="Source of assigned category: plaid, merchant_override, zapp_mapping, user_override",
    )
    behavioral_tags = models.ManyToManyField(
        "TransactionBehavioralTag",
        through="BankTransactionBehavioralTag",
        related_name="bank_transactions",
        blank=True,
    )

    raw_payload = EncryptedJSONField(default=dict, blank=True)
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

    @property
    def effective_category(self) -> str:
        """
        Resolved category per precedence:
        user_override_category > zapp_subcategory > zapp_primary_category > plaid category
        """
        if self.user_override_category:
            return self.user_override_category
        if self.zapp_subcategory and self.zapp_primary_category:
            try:
                sub = ZappSubcategory(self.zapp_subcategory)
                prim = ZappPrimaryCategory(self.zapp_primary_category)
                return f"{prim.label} / {sub.label}"
            except (ValueError, KeyError):
                pass
        if self.zapp_primary_category:
            try:
                return ZappPrimaryCategory(self.zapp_primary_category).label
            except (ValueError, KeyError):
                pass
        return self.category_primary or self.category_detailed or ""


class MerchantCategoryRule(models.Model):
    """
    Merchant-to-Zapp-category override rules.
    When a transaction matches a rule (by merchant pattern), use the rule's
    primary/subcategory instead of Plaid-derived Zapp category.
    """

    match_pattern = models.CharField(
        max_length=256,
        help_text="Substring or pattern to match against normalized merchant/name",
    )
    zapp_primary_category = models.CharField(
        max_length=64,
        choices=ZappPrimaryCategory.choices,
    )
    zapp_subcategory = models.CharField(
        max_length=64,
        choices=ZappSubcategory.choices,
        blank=True,
    )
    priority = models.PositiveSmallIntegerField(
        default=0,
        help_text="Higher = applied first; use for more specific overrides",
    )
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-priority", "match_pattern"]
        indexes = [models.Index(fields=["is_active", "priority"])]

    def __str__(self) -> str:
        return f"{self.match_pattern} -> {self.zapp_primary_category}"


class TransactionBehavioralTag(models.Model):
    """
    Behavioral tags for BankTransaction (e.g. Essential, Recurring, Impulse).
    Many-to-many: one transaction can have multiple tags.
    """

    name = models.CharField(max_length=64, unique=True)
    slug = models.SlugField(max_length=64, unique=True)
    description = models.CharField(max_length=256, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name


class BankTransactionBehavioralTag(models.Model):
    """Through model for BankTransaction <-> TransactionBehavioralTag."""

    transaction = models.ForeignKey(
        BankTransaction,
        on_delete=models.CASCADE,
        related_name="behavioral_tags_link",
    )
    tag = models.ForeignKey(
        TransactionBehavioralTag,
        on_delete=models.CASCADE,
        related_name="transactions",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [["transaction", "tag"]]
