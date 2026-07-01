import core.security.encrypted_fields
from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("transactions", "0010_transaction_persisted_value_score"),
    ]

    operations = [
        migrations.AlterField(
            model_name="transaction",
            name="personal_value_score",
            field=core.security.encrypted_fields.EncryptedIntegerField(
                blank=True,
                help_text="0-150 persisted model-backed value score for this transaction",
                null=True,
            ),
        ),
        migrations.AlterField(
            model_name="transaction",
            name="value_score_confidence",
            field=core.security.encrypted_fields.EncryptedFloatField(
                blank=True,
                help_text="0-1 confidence in personal_value_score",
                null=True,
            ),
        ),
    ]
