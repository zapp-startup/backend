from rest_framework import serializers

from .models import Badge, Group, GroupInvite, GroupMember, MonthlyTarget, PointEvent, UserBadge, UserStreak


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
    class Meta:
        model = UserStreak
        fields = "__all__"
        read_only_fields = ("user", "updated_at")


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
