from django.contrib import admin
from .models import Transaction, TransactionReflection


@admin.register(Transaction)
class TransactionAdmin(admin.ModelAdmin):
    list_display = (
        "user",
        "direction",
        "amount",
        "currency",
        "category",
        "occurred_at",
        "merchant",
        "subscription",
    )
    list_filter = ("direction", "category", "currency", "payment_channel")
    search_fields = ("description_raw", "merchant__name", "user__username")
    autocomplete_fields = ("merchant", "subscription")
    date_hierarchy = "occurred_at"


@admin.register(TransactionReflection)
class TransactionReflectionAdmin(admin.ModelAdmin):
    list_display = ("user", "transaction", "reflected_at", "reflected_same_day", "was_worth_it")
    search_fields = ("user__username", "transaction__description_raw")
    list_filter = ("reflected_same_day", "was_worth_it")
    autocomplete_fields = ("transaction",)
