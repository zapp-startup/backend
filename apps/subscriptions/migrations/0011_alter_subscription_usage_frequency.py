from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import migrations

import core.security.encrypted_fields


class Migration(migrations.Migration):
    dependencies = [
        ("subscriptions", "0010_merge_20260410_0001"),
    ]

    operations = [
        migrations.AlterField(
            model_name="subscription",
            name="usage_frequency",
            field=core.security.encrypted_fields.EncryptedFloatField(
                blank=True,
                help_text="0-1 normalized usage intensity (e.g. Spotify: active listening days / 30).",
                null=True,
                validators=[MinValueValidator(0.0), MaxValueValidator(1.0)],
            ),
        ),
    ]
