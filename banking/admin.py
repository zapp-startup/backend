from django.contrib import admin

from .models import BankAccount, BankConnection, BankTransaction, MerchantCategoryRule, TransactionBehavioralTag


@admin.register(BankConnection)
class BankConnectionAdmin(admin.ModelAdmin):
    list_display = ["id", "user", "institution_name", "status", "last_synced_at", "created_at"]
    list_filter = ["status"]
    search_fields = ["user__email", "institution_name", "plaid_item_id"]
    readonly_fields = ["plaid_item_id", "created_at", "updated_at"]
    # Do not show plaid_access_token in admin by default for security
    exclude = ["plaid_access_token"]


@admin.register(BankAccount)
class BankAccountAdmin(admin.ModelAdmin):
    list_display = ["id", "connection", "name", "mask", "type", "current_balance", "iso_currency_code"]
    list_filter = ["type"]
    search_fields = ["name", "plaid_account_id"]


@admin.register(BankTransaction)
class BankTransactionAdmin(admin.ModelAdmin):
    list_display = [
        "id",
        "user",
        "name",
        "amount",
        "date",
        "zapp_primary_category",
        "pending",
        "removed",
        "account",
    ]
    list_filter = ["pending", "removed", "date", "zapp_primary_category"]
    search_fields = ["name", "merchant_name", "plaid_transaction_id"]
    date_hierarchy = "date"


@admin.register(MerchantCategoryRule)
class MerchantCategoryRuleAdmin(admin.ModelAdmin):
    list_display = ["match_pattern", "zapp_primary_category", "zapp_subcategory", "priority", "is_active"]
    list_filter = ["zapp_primary_category", "is_active"]
    search_fields = ["match_pattern"]


@admin.register(TransactionBehavioralTag)
class TransactionBehavioralTagAdmin(admin.ModelAdmin):
    list_display = ["name", "slug"]
    search_fields = ["name"]
