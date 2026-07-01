from django.contrib import admin

from .models import WaitlistSignup


@admin.register(WaitlistSignup)
class WaitlistSignupAdmin(admin.ModelAdmin):
    list_display = ("email", "name", "source", "created_at")
    search_fields = ("email", "name", "source")
    ordering = ("-created_at",)
