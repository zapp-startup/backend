from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin
from .models import (
    User,
    UserRawExplicit,
    UserRawInferred,
    UserComputed,
    UserPreference,
)

@admin.register(User)
class UserAdmin(DjangoUserAdmin):
    list_display = ("username", "email", "supabase_uid", "is_staff", "is_active")
    search_fields = ("username", "email", "supabase_uid")

admin.site.register(UserRawExplicit)
admin.site.register(UserRawInferred)
admin.site.register(UserComputed)
admin.site.register(UserPreference)
