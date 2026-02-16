from rest_framework import serializers
from .models import Group, GroupMember, Season, PointEvent, UserStreak


class GroupSerializer(serializers.ModelSerializer):
    class Meta:
        model = Group
        fields = "__all__"
        read_only_fields = ("created_by", "invite_code", "created_at")


class GroupMemberSerializer(serializers.ModelSerializer):
    class Meta:
        model = GroupMember
        fields = "__all__"
        read_only_fields = ("joined_at",)


class SeasonSerializer(serializers.ModelSerializer):
    class Meta:
        model = Season
        fields = "__all__"


class PointEventSerializer(serializers.ModelSerializer):
    class Meta:
        model = PointEvent
        fields = "__all__"
        read_only_fields = ("created_at", "user")


class UserStreakSerializer(serializers.ModelSerializer):
    class Meta:
        model = UserStreak
        fields = "__all__"
        read_only_fields = ("user", "updated_at")
