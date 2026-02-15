from __future__ import annotations

from django.conf import settings
from django.db import models
import uuid
from django.contrib.auth.models import AbstractUser

class User(AbstractUser):
    """Custom user model with UUID primary key for Supabase compatibility"""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    
    class Meta:
        db_table = 'auth_user'

    def __str__(self)->str:
        return self.username


# ----------------------------
# Existing enums (keep)
# ----------------------------
class FinancialGoal(models.TextChoices):
    SAVE_MORE = "save_more", "Save more"
    INVEST = "invest", "Invest"
    REDUCE_DEBT = "reduce_debt", "Reduce debt"
    BUILD_CREDIT = "build_credit", "Build credit"
    CONTROL_SUBS = "control_subs", "Control subscriptions"
    OTHER = "other", "Other"


class RiskTolerance(models.TextChoices):
    LOW = "low", "Low"
    MEDIUM = "medium", "Medium"
    HIGH = "high", "High"


class BudgetStyle(models.TextChoices):
    STRICT = "strict", "Strict"
    FLEXIBLE = "flexible", "Flexible"
    OPTIMIZE_VALUE = "optimize_value", "Optimize value"


class PreferenceValueType(models.TextChoices):
    BOOL = "bool", "Bool"
    INT = "int", "Int"
    FLOAT = "float", "Float"
    STRING = "string", "String"
    JSON = "json", "JSON"


class PreferenceSource(models.TextChoices):
    CHAT = "chat", "Chat"
    ONBOARDING = "onboarding", "Onboarding"
    INFERRED = "inferred", "Inferred"
    MANUAL = "manual", "Manual"


# ----------------------------
# NEW: 3-layer user tables
# ----------------------------

class UserRawExplicit(models.Model):
    """
    Raw data asked directly from the user (onboarding/survey).
    One row per user.
    """

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="raw_explicit",
        primary_key=True,
    )

    # QoL / identity
    display_name = models.CharField(max_length=64, blank=True)

    # Demographics / structural
    dob = models.DateField(blank=True, null=True)
    age_range = models.CharField(max_length=32, blank=True)  # optional alt to dob
    household_size = models.PositiveSmallIntegerField(blank=True, null=True)
    location_zip = models.CharField(max_length=16, blank=True)

    life_stage = models.CharField(max_length=32, blank=True)        # e.g., student/early_career
    employment_type = models.CharField(max_length=32, blank=True)
    dependents_count = models.PositiveSmallIntegerField(blank=True, null=True)

    # Income inputs (explicit raw)
    income_range = models.CharField(max_length=32, blank=True)
    monthly_income = models.DecimalField(max_digits=12, decimal_places=2, blank=True, null=True)
    monthly_fixed_expenses = models.DecimalField(max_digits=12, decimal_places=2, blank=True, null=True)

    # Goals / preferences (explicit)
    financial_goal = models.CharField(
        max_length=32, choices=FinancialGoal.choices, blank=True
    )
    risk_tolerance = models.CharField(
        max_length=16, choices=RiskTolerance.choices, blank=True
    )
    budget_style = models.CharField(
        max_length=32, choices=BudgetStyle.choices, blank=True
    )

    # Value-priority sliders (explicit raw)
    # Recommended: 0–100 scale
    value_priority_cost = models.PositiveSmallIntegerField(blank=True, null=True)
    value_priority_quality = models.PositiveSmallIntegerField(blank=True, null=True)
    value_priority_sustainability = models.PositiveSmallIntegerField(blank=True, null=True)

    # Optional onboarding baseline (keep if you want)
    # Regret frequency should NOT be here since you measure from transaction popups.
    self_report_research_habit = models.PositiveSmallIntegerField(blank=True, null=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=["financial_goal"]),
            models.Index(fields=["risk_tolerance"]),
            models.Index(fields=["budget_style"]),
            models.Index(fields=["income_range"]),
        ]

    def __str__(self) -> str:
        return f"{self.user} raw_explicit"


class UserRawInferred(models.Model):
    """
    Raw-ish inferred aggregates from behavior/spending patterns.
    Treat as a snapshot computed over a time window (default 90d).
    One row per user (latest snapshot).
    """

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="raw_inferred",
        primary_key=True,
    )

    # Snapshot window for these metrics
    window_days = models.PositiveSmallIntegerField(default=90)

    # Examples (keep as nullable since you’ll fill progressively)
    avg_purchase_price = models.DecimalField(max_digits=12, decimal_places=2, blank=True, null=True)
    purchase_price_variance = models.DecimalField(max_digits=18, decimal_places=6, blank=True, null=True)

    category_distribution_json = models.JSONField(default=dict, blank=True)

    percent_impulsive_purchases = models.FloatField(blank=True, null=True)  # 0..1
    regret_frequency = models.FloatField(blank=True, null=True)             # 0..1 fraction of regretful txns
    brand_repetition_rate = models.FloatField(blank=True, null=True)        # 0..1
    late_night_purchase_frequency = models.FloatField(blank=True, null=True)  # 0..1

    avg_decision_time_minutes = models.FloatField(blank=True, null=True)

    # Subscription aggregates (optional)
    active_subscriptions_count = models.IntegerField(blank=True, null=True)
    total_subscription_cost = models.DecimalField(max_digits=12, decimal_places=2, blank=True, null=True)
    percent_income_spent_on_subscriptions = models.FloatField(blank=True, null=True)  # 0..1

    subscription_usage_frequency_json = models.JSONField(default=dict, blank=True)
    cancel_reactivation_frequency = models.FloatField(blank=True, null=True)

    # Budget aggregates (optional)
    actual_monthly_spending = models.DecimalField(max_digits=12, decimal_places=2, blank=True, null=True)

    computed_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=["window_days", "computed_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.user} raw_inferred ({self.window_days}d)"


class UserComputed(models.Model):
    """
    Processed, model-ready metrics used by valuation/personalization.
    One row per user.
    """

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="computed",
        primary_key=True,
    )

    # Labels / personas
    spending_personality = models.CharField(max_length=64, blank=True)
    product_spending_style = models.CharField(max_length=16, blank=True)      # Planned/Mixed/Impulsive
    subscription_behavior_type = models.CharField(max_length=32, blank=True)  # Over-subscribed/Churn-heavy/etc

    # Normalized weights (0..1, should sum ~ 1 when present)
    cost_weight = models.FloatField(blank=True, null=True)
    quality_weight = models.FloatField(blank=True, null=True)
    sustainability_weight = models.FloatField(blank=True, null=True)

    # Core computed scores (0..1)
    impulse_susceptibility_score = models.FloatField(blank=True, null=True)
    regret_sensitivity = models.FloatField(blank=True, null=True)
    budget_adherence_score = models.FloatField(blank=True, null=True)

    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=["spending_personality"]),
            models.Index(fields=["product_spending_style"]),
            models.Index(fields=["subscription_behavior_type"]),
        ]

    def __str__(self) -> str:
        return f"{self.user} computed"


# ----------------------------
# OPTIONAL: Keep this KV table
# ----------------------------
class UserPreference(models.Model):
    """
    Optional: generic KV store for extra knobs / experimental preferences.
    (Not required for the 3-table format, but useful.)
    """

    id = models.BigAutoField(primary_key=True)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="preferences",
    )

    key = models.CharField(max_length=64)

    value_type = models.CharField(
        max_length=16,
        choices=PreferenceValueType.choices,
        default=PreferenceValueType.STRING,
    )

    value_json = models.JSONField(default=dict, blank=True)

    source = models.CharField(
        max_length=16,
        choices=PreferenceSource.choices,
        default=PreferenceSource.MANUAL,
    )

    confidence = models.FloatField(default=1.0)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["user", "key"], name="uniq_user_preference_key")
        ]
        indexes = [
            models.Index(fields=["user", "key"]),
            models.Index(fields=["user", "updated_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.user} pref {self.key}"
