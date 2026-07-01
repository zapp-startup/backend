from django.contrib import admin

from .models import AuditEvent, FinancialConsent


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


@admin.register(AuditEvent)
class AuditEventAdmin(admin.ModelAdmin):
    list_display = ["id", "event_name", "outcome", "user", "resource_type", "resource_id", "occurred_at"]
    list_filter = ["outcome", "actor_type", "source_system", "resource_type"]
    readonly_fields = [f.name for f in AuditEvent._meta.fields]
    search_fields = ["event_name", "user__email", "user__username", "request_id", "resource_id"]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
