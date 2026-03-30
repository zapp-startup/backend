from django.contrib import admin

from .models import FinancialConsent


@admin.register(FinancialConsent)
class FinancialConsentAdmin(admin.ModelAdmin):
    list_display = ["id", "user", "consent_type", "policy_version", "accepted_at", "source"]
    list_filter = ["consent_type", "policy_version"]
    readonly_fields = [f.name for f in FinancialConsent._meta.fields]
    search_fields = ["user__email", "user__username"]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
