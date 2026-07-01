from django.conf import settings
from django.db import models
from django.utils import timezone


class GroupRole(models.TextChoices):
    ADMIN = "admin", "Admin"
    MEMBER = "member", "Member"


class GroupInviteStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    ACCEPTED = "accepted", "Accepted"
    DECLINED = "declined", "Declined"
    REVOKED = "revoked", "Revoked"


class PointAction(models.TextChoices):
    LOG_PURCHASE = "log_purchase", "Log purchase"
    LOG_PURCHASE_NEW_DAY = "log_purchase_new_day", "Log purchase on a new day"
    REFLECT_SAME_DAY = "reflect_same_day", "Reflect same day"
    REFLECT_RISKY_PURCHASE = "reflect_risky_purchase", "Reflect on risky purchase"
    USE_ADVISOR = "use_advisor", "Use advisor"
    FIRST_ADVISOR_USE = "first_advisor_use", "First advisor use"
    WEEKLY_REVIEW = "weekly_review", "Complete weekly review"
    ADD_SUBSCRIPTION = "add_subscription", "Add subscription"
    CANCEL_SUBSCRIPTION = "cancel_subscription", "Cancel subscription"
    PAUSE_SUBSCRIPTION = "pause_subscription", "Pause subscription"
    COMPLETE_ONBOARDING = "complete_onboarding", "Complete onboarding"
    SET_MONTHLY_TARGET = "set_monthly_target", "Set monthly target"
    COMPLETE_MONTHLY_TARGET = "complete_monthly_target", "Complete monthly target"
    COMPLETE_MONTHLY_REVIEW = "complete_monthly_review", "Complete monthly review"
    REVIEW_UPCOMING_RENEWAL = "review_upcoming_renewal", "Review upcoming renewal"
    COMPLETE_SUBSCRIPTION_AUDIT = "complete_subscription_audit", "Complete subscription audit"
    RUN_SUBSCRIPTION_VALUATION = "run_subscription_valuation", "Run subscription valuation"
    RUN_ITEM_VALUATION = "run_item_valuation", "Run item valuation"
    FOLLOW_WAIT_RECOMMENDATION = "follow_wait_recommendation", "Follow wait or skip recommendation"
    JOIN_GROUP = "join_group", "Join group"
    CREATE_GROUP = "create_group", "Create group"
    LEADERBOARD_TOP_THREE = "leaderboard_top_three", "Finish in weekly top 3"
    LEADERBOARD_WINNER = "leaderboard_winner", "Finish first in weekly leaderboard"
    STREAK_7_BONUS = "streak_7_bonus", "7-day streak bonus"
    STREAK_14_BONUS = "streak_14_bonus", "14-day streak bonus"
    STREAK_30_BONUS = "streak_30_bonus", "30-day streak bonus"


class BadgeCategory(models.TextChoices):
    ONBOARDING = "onboarding", "Onboarding"
    TRANSACTIONS = "transactions", "Transactions"
    REFLECTIONS = "reflections", "Reflections"
    ADVISOR = "advisor", "Advisor"
    SUBSCRIPTIONS = "subscriptions", "Subscriptions"
    SOCIAL = "social", "Social"
    STREAKS = "streaks", "Streaks"
    MONTHLY_GOALS = "monthly_goals", "Monthly goals"
    IMPROVEMENT = "improvement", "Improvement"


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
        constraints = [
            models.UniqueConstraint(fields=["group", "user"], name="uniq_group_member"),
        ]
        indexes = [
            models.Index(fields=["group", "joined_at"]),
            models.Index(fields=["user", "joined_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.user} in {self.group}"


class GroupInvite(models.Model):
    id = models.BigAutoField(primary_key=True)
    group = models.ForeignKey(
        Group,
        on_delete=models.CASCADE,
        related_name="invites",
    )
    invited_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="group_invites_sent",
    )
    invited_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="group_invites_received",
        null=True,
        blank=True,
    )
    invite_code = models.CharField(max_length=32, unique=True)
    status = models.CharField(
        max_length=16,
        choices=GroupInviteStatus.choices,
        default=GroupInviteStatus.PENDING,
    )
    note = models.CharField(max_length=255, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    accepted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="accepted_group_invites",
        null=True,
        blank=True,
    )
    accepted_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=["group", "status", "created_at"]),
            models.Index(fields=["invited_user", "status", "created_at"]),
        ]

    def is_active(self, now=None) -> bool:
        now = now or timezone.now()
        if self.status != GroupInviteStatus.PENDING:
            return False
        if self.expires_at is None:
            return True
        return self.expires_at > now

    def __str__(self) -> str:
        return f"{self.group} invite {self.invite_code}"


class MonthlyTargetStatus(models.TextChoices):
    ACTIVE = "active", "Active"
    COMPLETED = "completed", "Completed"
    EXPIRED = "expired", "Expired"


class PeriodicReviewType(models.TextChoices):
    WEEKLY = "weekly", "Weekly"
    MONTHLY = "monthly", "Monthly"


class PeriodicReviewStatus(models.TextChoices):
    OPEN = "open", "Open"
    COMPLETED = "completed", "Completed"


class MonthlyTarget(models.Model):
    id = models.BigAutoField(primary_key=True)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="monthly_targets",
    )
    target_type = models.CharField(max_length=64)
    title = models.CharField(max_length=128)
    month_start = models.DateField()
    target_value = models.PositiveIntegerField()
    current_value = models.PositiveIntegerField(default=0)
    status = models.CharField(
        max_length=16,
        choices=MonthlyTargetStatus.choices,
        default=MonthlyTargetStatus.ACTIVE,
    )
    completed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["user", "target_type", "month_start"], name="uniq_user_target_month"),
        ]
        indexes = [
            models.Index(fields=["user", "month_start", "status"]),
            models.Index(fields=["target_type", "month_start"]),
        ]

    def __str__(self) -> str:
        return f"{self.user} {self.target_type} {self.month_start}"


class PeriodicReview(models.Model):
    id = models.BigAutoField(primary_key=True)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="periodic_reviews",
    )
    review_type = models.CharField(max_length=16, choices=PeriodicReviewType.choices)
    period_start = models.DateField()
    period_end = models.DateField()
    status = models.CharField(
        max_length=16,
        choices=PeriodicReviewStatus.choices,
        default=PeriodicReviewStatus.OPEN,
    )
    summary_json = models.JSONField(default=dict, blank=True)
    notes = models.TextField(blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["user", "review_type", "period_start"],
                name="uniq_user_periodic_review",
            ),
        ]
        indexes = [
            models.Index(fields=["user", "review_type", "status"]),
            models.Index(fields=["period_start", "period_end"]),
        ]
        ordering = ["-period_start", "-created_at"]

    def __str__(self) -> str:
        return f"{self.user} {self.review_type} review {self.period_start}→{self.period_end}"


class PointEvent(models.Model):
    """
    Backend-owned point ledger. Business logic should create every row.
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
        blank=True,
    )
    action = models.CharField(max_length=48, choices=PointAction.choices)
    points = models.IntegerField()
    source_object_type = models.CharField(max_length=32, blank=True)
    source_object_id = models.BigIntegerField(null=True, blank=True)
    event_key = models.CharField(max_length=255, null=True, blank=True, unique=True)
    metadata_json = models.JSONField(default=dict, blank=True)
    window_date = models.DateField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=["user", "created_at"]),
            models.Index(fields=["user", "action", "created_at"]),
            models.Index(fields=["group", "created_at"]),
            models.Index(fields=["group", "action", "created_at"]),
            models.Index(fields=["user", "window_date"]),
            models.Index(fields=["group", "window_date"]),
        ]

    def save(self, *args, **kwargs):
        if self.event_key == "":
            self.event_key = None
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return f"{self.user} +{self.points} ({self.action})"


class UserStreak(models.Model):
    """
    Single daily streak updated from backend-awarded point events.
    """

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="streak",
        primary_key=True,
    )
    total_points_earned = models.PositiveIntegerField(default=0)
    current_streak_days = models.PositiveIntegerField(default=0)
    best_streak_days = models.PositiveIntegerField(default=0)
    last_checkin_date = models.DateField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self) -> str:
        return f"{self.user} streak {self.current_streak_days}"


class Badge(models.Model):
    id = models.BigAutoField(primary_key=True)
    code = models.CharField(max_length=64, unique=True)
    name = models.CharField(max_length=128)
    description = models.TextField()
    icon = models.CharField(max_length=64, blank=True)
    category = models.CharField(max_length=32, choices=BadgeCategory.choices)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=["category", "is_active"]),
        ]

    def __str__(self) -> str:
        return self.name


class UserBadge(models.Model):
    id = models.BigAutoField(primary_key=True)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="badges",
    )
    badge = models.ForeignKey(
        Badge,
        on_delete=models.CASCADE,
        related_name="user_badges",
    )
    awarded_at = models.DateTimeField(auto_now_add=True)
    source_object_type = models.CharField(max_length=32, blank=True)
    source_object_id = models.BigIntegerField(null=True, blank=True)
    trigger_event = models.ForeignKey(
        PointEvent,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="triggered_badges",
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["user", "badge"], name="uniq_user_badge"),
        ]
        indexes = [
            models.Index(fields=["user", "awarded_at"]),
            models.Index(fields=["badge", "awarded_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.user} earned {self.badge.code}"
