from django.contrib import admin
from .models import Transaction


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
