from django.conf import settings
from django.db import models


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


class UserProfile(models.Model):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="profile",
        primary_key=True,
    )

    display_name = models.CharField(max_length=64, blank=True)

    age_range = models.CharField(max_length=32, blank=True)
    household_size = models.PositiveSmallIntegerField(null=True, blank=True)
    location_zip = models.CharField(max_length=16, blank=True)

    income_range = models.CharField(max_length=32, blank=True)

    financial_goal = models.CharField(
        max_length=32,
        choices=FinancialGoal.choices,
        blank=True,
    )

    risk_tolerance = models.CharField(
        max_length=16,
        choices=RiskTolerance.choices,
        blank=True,
    )

    budget_style = models.CharField(
        max_length=32,
        choices=BudgetStyle.choices,
        blank=True,
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=["financial_goal"]),
            models.Index(fields=["risk_tolerance"]),
            models.Index(fields=["budget_style"]),
        ]

    def __str__(self) -> str:
        return f"{self.user} profile"


class UserPreference(models.Model):
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
        unique_together = ("user", "key")
        indexes = [
            models.Index(fields=["user", "key"]),
            models.Index(fields=["user", "updated_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.user} pref {self.key}"
