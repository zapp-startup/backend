from django.contrib import admin
from .models import Group, GroupMember, Season, PointEvent, UserStreak


@admin.register(Group)
class GroupAdmin(admin.ModelAdmin):
    list_display = ("name", "invite_code", "created_by", "is_private", "created_at")
    search_fields = ("name", "invite_code", "created_by__username")
    list_filter = ("is_private",)


@admin.register(GroupMember)
class GroupMemberAdmin(admin.ModelAdmin):
    list_display = ("group", "user", "role", "joined_at")
    search_fields = ("group__name", "user__username")
    list_filter = ("role",)
    autocomplete_fields = ("group", "user")


@admin.register(Season)
class SeasonAdmin(admin.ModelAdmin):
    list_display = ("season_type", "start_at", "end_at", "created_at")
    list_filter = ("season_type",)


@admin.register(PointEvent)
class PointEventAdmin(admin.ModelAdmin):
    list_display = ("user", "group", "action", "points", "created_at")
    search_fields = ("user__username", "action", "source_object_type")
    list_filter = ("action",)
    autocomplete_fields = ("user", "group")


@admin.register(UserStreak)
class UserStreakAdmin(admin.ModelAdmin):
    list_display = ("user", "current_streak_days", "best_streak_days", "last_checkin_date", "updated_at")
    search_fields = ("user__username",)
