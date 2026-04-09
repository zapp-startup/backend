from __future__ import annotations

from django.apps import apps
from django.core.management.base import BaseCommand


MODEL_FIELDS: dict[str, list[str]] = {
    "users.UserRawExplicit": [
        "display_name",
        "dob",
        "age_range",
        "household_size",
        "location_zip",
        "life_stage",
        "employment_type",
        "dependents_count",
        "income_range",
        "monthly_income",
        "monthly_fixed_expenses",
        "financial_goal",
        "risk_tolerance",
        "budget_style",
        "value_priority_cost",
        "value_priority_quality",
        "value_priority_sustainability",
        "self_report_research_habit",
    ],
    "users.UserRawInferred": [
        "avg_purchase_price",
        "purchase_price_variance",
        "category_distribution_json",
        "percent_impulsive_purchases",
        "regret_frequency",
        "brand_repetition_rate",
        "late_night_purchase_frequency",
        "avg_decision_time_minutes",
        "active_subscriptions_count",
        "total_subscription_cost",
        "percent_income_spent_on_subscriptions",
        "subscription_usage_frequency_json",
        "cancel_reactivation_frequency",
        "actual_monthly_spending",
    ],
    "users.UserPreference": [
        "value_json",
    ],
    "transactions.Transaction": [
        "amount",
        "description_raw",
        "satisfaction_rating",
        "regret_rating",
        "repurchase_likelihood",
        "usage_frequency",
        "reflection_text",
        "considered_at",
        "self_report_researched",
        "impulse_score",
        "regret_score",
        "feedback_value_score",
        "feedback_confidence",
    ],
    "transactions.TransactionReflection": [
        "regret_score",
        "was_worth_it",
        "notes",
    ],
    "subscriptions.Subscription": [
        "plan_name",
        "price",
        "started_on",
        "renewal_date",
        "cancelled_on",
        "notes",
        "usage_frequency",
        "reactivation_count",
        "feedback_value_score",
        "feedback_confidence",
        "subscription_utilization",
        "subscription_cost_benefit",
    ],
    "banking.BankConnection": [
        "institution_name",
        "sync_cursor",
    ],
    "banking.BankAccount": [
        "name",
        "official_name",
        "mask",
        "current_balance",
        "available_balance",
        "raw_payload",
    ],
    "banking.BankTransaction": [
        "name",
        "merchant_name",
        "amount",
        "authorized_date",
        "category_primary",
        "category_detailed",
        "zapp_subcategory",
        "user_override_category",
        "category_source",
        "raw_payload",
    ],
}


class Command(BaseCommand):
    help = "Rewrite legacy plaintext values into the new encrypted app-data fields."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="Report rows that would be rewritten.")

    def handle(self, *args, **options):
        dry_run = bool(options["dry_run"])
        total_rows = 0

        for model_label, fields in MODEL_FIELDS.items():
            model = apps.get_model(model_label)
            rewritten = 0
            inspected = 0
            for obj in model.objects.all().iterator():
                inspected += 1
                if not dry_run:
                    obj.save(update_fields=fields)
                rewritten += 1
            total_rows += rewritten
            mode = "Would rewrite" if dry_run else "Rewrote"
            self.stdout.write(f"{mode} {rewritten} rows for {model_label} ({inspected} inspected).")

        self.stdout.write(self.style.SUCCESS(f"Processed {total_rows} rows across encrypted models."))
