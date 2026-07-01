# Generated manually for synthetic data realism patch

from django.db import migrations, models

from apps.subscriptions.models import SubscriptionEligibility


def backfill_subscription_eligibility(apps, schema_editor):
    Merchant = apps.get_model("subscriptions", "Merchant")
    try:
        from datagen.config import MERCHANT_CATALOG
    except Exception:  # pragma: no cover
        MERCHANT_CATALOG = []
    name_to_elig = {
        m["name"]: m.get("eligibility", SubscriptionEligibility.NOT_SUBSCRIBABLE)
        for m in MERCHANT_CATALOG
    }
    for m in Merchant.objects.all():
        elig = name_to_elig.get(m.name, SubscriptionEligibility.NOT_SUBSCRIBABLE)
        if getattr(m, "subscription_eligibility", None) != elig:
            m.subscription_eligibility = elig
            m.save(update_fields=["subscription_eligibility"])


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("subscriptions", "0005_add_feedback_and_valuation_fields"),
    ]

    operations = [
        migrations.AddField(
            model_name="merchant",
            name="subscription_eligibility",
            field=models.CharField(
                choices=SubscriptionEligibility.choices,
                default=SubscriptionEligibility.NOT_SUBSCRIBABLE,
                help_text="Whether this merchant can appear as a recurring subscription in synthetic data.",
                max_length=32,
            ),
        ),
        migrations.RunPython(backfill_subscription_eligibility, noop_reverse),
    ]
