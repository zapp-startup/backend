from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("transactions", "0009_transaction_bank_transaction"),
    ]

    operations = [
        migrations.AddField(
            model_name="transaction",
            name="personal_value_score",
            field=models.IntegerField(
                blank=True,
                help_text="0-150 persisted model-backed value score for this transaction",
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="transaction",
            name="value_score_computed_at",
            field=models.DateTimeField(
                blank=True,
                help_text="When personal_value_score was last computed",
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="transaction",
            name="value_score_confidence",
            field=models.FloatField(
                blank=True,
                help_text="0-1 confidence in personal_value_score",
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="transaction",
            name="value_score_model_version",
            field=models.CharField(
                blank=True,
                help_text="Model version used to produce personal_value_score",
                max_length=64,
            ),
        ),
    ]
