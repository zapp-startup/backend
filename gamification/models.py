from django.conf import settings
from django.db import models
from django.utils import timezone


class GroupRole(models.TextChoices):
    ADMIN = "admin", "Admin"
    MEMBER = "member", "Member"


class SeasonType(models.TextChoices):
    WEEKLY = "weekly", "Weekly"


class PointAction(models.TextChoices):
    LOG_PURCHASE = "log_purchase", "Log purchase"
    REFLECT_SAME_DAY = "reflect_same_day", "Reflect same day"
    USE_ADVISOR = "use_advisor", "Use buy advisor"
    WEEKLY_REVIEW = "weekly_review", "Complete weekly review"
    ADD_SUBSCRIPTION = "add_subscription", "Add subscription"
    CANCEL_SUBSCRIPTION = "cancel_subscription", "Cancel/pause subscription"


class Group(models.Model):
    id = models.BigAutoField(primary_key=True)

    name = models.CharField(max_length=64)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="created_groups",
    )

    invite_code = models.CharField(max_length=12, unique=True)
    is_private = models.BooleanField(default=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=["invite_code"]),
            models.Index(fields=["created_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.name} ({self.invite_code})"


class GroupMember(models.Model):
    id = models.BigAutoField(primary_key=True)

    group = models.ForeignKey(
        Group,
        on_delete=models.CASCADE,
        related_name="memberships",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="group_memberships",
    )
    role = models.CharField(
        max_length=16,
        choices=GroupRole.choices,
        default=GroupRole.MEMBER,
    )
    joined_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("group", "user")
        indexes = [
            models.Index(fields=["group", "joined_at"]),
            models.Index(fields=["user", "joined_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.user} in {self.group}"


class Season(models.Model):
    """
    Defines a competition window. MVP: weekly seasons only.
    """
    id = models.BigAutoField(primary_key=True)

    season_type = models.CharField(
        max_length=16,
        choices=SeasonType.choices,
        default=SeasonType.WEEKLY,
    )
    start_at = models.DateTimeField()
    end_at = models.DateTimeField()

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("season_type", "start_at", "end_at")
        indexes = [
            models.Index(fields=["season_type", "start_at"]),
            models.Index(fields=["end_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.season_type} {self.start_at.date()}→{self.end_at.date()}"


class PointEvent(models.Model):
    """
    Single source of truth for points. Everything aggregates from here.
    """
    id = models.BigAutoField(primary_key=True)

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="point_events",
    )
    group = models.ForeignKey(
        Group,
        on_delete=models.CASCADE,
        related_name="point_events",
        null=True,
        blank=True,  # null group = self-only points (still counts for streak)
    )

    action = models.CharField(
        max_length=32,
        choices=PointAction.choices,
    )
    points = models.IntegerField()

    # Optional linkage to what triggered this (transaction/subscription/etc.)
    source_object_type = models.CharField(max_length=32, blank=True)
    source_object_id = models.BigIntegerField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=["user", "created_at"]),
            models.Index(fields=["group", "created_at"]),
            models.Index(fields=["user", "action", "created_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.user} +{self.points} ({self.action})"


class UserStreak(models.Model):
    """
    ONE streak only: Daily check-in streak.
    A check-in occurs if user performs any points action that day.
    """
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="streak",
        primary_key=True,
    )

    current_streak_days = models.PositiveIntegerField(default=0)
    best_streak_days = models.PositiveIntegerField(default=0)
    last_checkin_date = models.DateField(null=True, blank=True)

    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self) -> str:
        return f"{self.user} streak {self.current_streak_days}"
