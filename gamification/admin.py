from django.contrib import admin

from .models import Badge, Group, GroupInvite, GroupMember, MonthlyTarget, PointEvent, UserBadge, UserStreak


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


@admin.register(GroupInvite)
class GroupInviteAdmin(admin.ModelAdmin):
    list_display = ("group", "invited_by", "invited_user", "invite_code", "status", "expires_at", "created_at")
    search_fields = ("group__name", "invite_code", "invited_by__username", "invited_user__username")
    list_filter = ("status",)
    autocomplete_fields = ("group", "invited_by", "invited_user", "accepted_by")


@admin.register(PointEvent)
class PointEventAdmin(admin.ModelAdmin):
    list_display = ("user", "group", "action", "points", "event_key", "window_date", "created_at")
    search_fields = ("user__username", "action", "source_object_type", "event_key")
    list_filter = ("action", "group")
    autocomplete_fields = ("user", "group")


@admin.register(UserStreak)
class UserStreakAdmin(admin.ModelAdmin):
    list_display = ("user", "total_points_earned", "current_streak_days", "best_streak_days", "last_checkin_date", "updated_at")
    search_fields = ("user__username",)


@admin.register(Badge)
class BadgeAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "category", "is_active", "created_at")
    search_fields = ("code", "name")
    list_filter = ("category", "is_active")


@admin.register(UserBadge)
class UserBadgeAdmin(admin.ModelAdmin):
    list_display = ("user", "badge", "awarded_at", "source_object_type", "source_object_id")
    search_fields = ("user__username", "badge__code", "badge__name")
    autocomplete_fields = ("user", "badge", "trigger_event")


@admin.register(MonthlyTarget)
class MonthlyTargetAdmin(admin.ModelAdmin):
    list_display = ("user", "target_type", "month_start", "target_value", "current_value", "status", "completed_at")
    search_fields = ("user__username", "target_type", "title")
    list_filter = ("status", "month_start")
