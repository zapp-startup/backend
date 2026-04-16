# Backfill legacy usage_frequency values + attach validators (no DB CHECK).
# Split from 0008 so ALTER TYPE commits before RunPython (Supabase/PG triggers).

from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import migrations, models


def scale_legacy_usage_frequency_to_0_1(apps, schema_editor):
    """
    Legacy 0-7 whole-number 'uses per week' → divide by 7.
    Values already in (0,1) with fractional parts are left clamped only.
    """
    Subscription = apps.get_model("subscriptions", "Subscription")
    for sub in Subscription.objects.exclude(usage_frequency__isnull=True).iterator():
        v = float(sub.usage_frequency)
        if v > 7:
            new_v = 1.0
        elif abs(v - round(v)) < 1e-9 and v <= 7:
            new_v = min(1.0, v / 7.0)
        else:
            new_v = max(0.0, min(1.0, v))
        Subscription.objects.filter(pk=sub.pk).update(usage_frequency=new_v)


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):
    atomic = False
    dependencies = [
        ("subscriptions", "0008_subscription_usage_frequency_float"),
    ]

    operations = [
        migrations.RunPython(scale_legacy_usage_frequency_to_0_1, noop_reverse),
        migrations.AlterField(
            model_name="subscription",
            name="usage_frequency",
            field=models.FloatField(
                blank=True,
                help_text="0-1 normalized usage intensity (e.g. Spotify: active listening days / 30).",
                null=True,
                validators=[MinValueValidator(0.0), MaxValueValidator(1.0)],
            ),
        ),
    ]
