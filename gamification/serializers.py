from rest_framework import serializers

from .models import Badge, Group, GroupInvite, GroupMember, MonthlyTarget, PointEvent, UserBadge, UserStreak
from .services import build_level_progress


class GroupSerializer(serializers.ModelSerializer):
    member_count = serializers.SerializerMethodField()

    def get_member_count(self, obj):
        return obj.memberships.count()

    class Meta:
        model = Group
        fields = "__all__"
        read_only_fields = ("created_by", "invite_code", "created_at")


class GroupMemberSerializer(serializers.ModelSerializer):
    username = serializers.CharField(source="user.username", read_only=True)

    class Meta:
        model = GroupMember
        fields = "__all__"
        read_only_fields = ("joined_at",)


class GroupInviteSerializer(serializers.ModelSerializer):
    class Meta:
        model = GroupInvite
        fields = "__all__"
        read_only_fields = (
            "invited_by",
            "invite_code",
            "status",
            "accepted_by",
            "accepted_at",
            "revoked_at",
            "created_at",
        )


class PointEventSerializer(serializers.ModelSerializer):
    class Meta:
        model = PointEvent
        fields = "__all__"
        read_only_fields = (
            "user",
            "group",
            "action",
            "points",
            "source_object_type",
            "source_object_id",
            "event_key",
            "metadata_json",
            "window_date",
            "created_at",
        )


class UserStreakSerializer(serializers.ModelSerializer):
    level = serializers.SerializerMethodField()
    level_floor_points = serializers.SerializerMethodField()
    next_level_points = serializers.SerializerMethodField()
    points_into_level = serializers.SerializerMethodField()
    points_to_next_level = serializers.SerializerMethodField()

    def _level_progress(self, obj):
        return build_level_progress(obj.total_points_earned)

    def get_level(self, obj):
        return self._level_progress(obj)["level"]

    def get_level_floor_points(self, obj):
        return self._level_progress(obj)["level_floor_points"]

    def get_next_level_points(self, obj):
        return self._level_progress(obj)["next_level_points"]

    def get_points_into_level(self, obj):
        return self._level_progress(obj)["points_into_level"]

    def get_points_to_next_level(self, obj):
        return self._level_progress(obj)["points_to_next_level"]

    class Meta:
        model = UserStreak
        fields = "__all__"
        read_only_fields = (
            "user",
            "total_points_earned",
            "updated_at",
            "level",
            "level_floor_points",
            "next_level_points",
            "points_into_level",
            "points_to_next_level",
        )


class BadgeSerializer(serializers.ModelSerializer):
    class Meta:
        model = Badge
        fields = "__all__"
        read_only_fields = ("id", "code", "name", "description", "icon", "category", "is_active", "created_at")


class UserBadgeSerializer(serializers.ModelSerializer):
    badge = BadgeSerializer(read_only=True)

    class Meta:
        model = UserBadge
        fields = "__all__"
        read_only_fields = (
            "id",
            "user",
            "badge",
            "awarded_at",
            "source_object_type",
            "source_object_id",
            "trigger_event",
        )


class MonthlyTargetSerializer(serializers.ModelSerializer):
    class Meta:
        model = MonthlyTarget
        fields = "__all__"
        read_only_fields = ("user", "current_value", "status", "completed_at", "created_at", "updated_at")
