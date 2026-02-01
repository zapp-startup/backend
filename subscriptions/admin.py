from django.contrib import admin
from .models import Merchant, Subscription


@admin.register(Merchant)
class MerchantAdmin(admin.ModelAdmin):
    list_display = ("name", "category", "website_domain", "created_at")
    search_fields = ("name", "website_domain")
    list_filter = ("category",)


@admin.register(Subscription)
class SubscriptionAdmin(admin.ModelAdmin):
    list_display = (
        "user",
        "merchant",
        "status",
        "billing_cycle",
        "price",
        "currency",
        "renewal_date",
    )
    list_filter = ("status", "billing_cycle", "currency")
    search_fields = ("merchant__name", "user__username")
    autocomplete_fields = ("merchant",)
from django.contrib import admin

# Register your models here.
