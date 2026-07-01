from django.db import migrations, models

import core.security.encrypted_fields


class Migration(migrations.Migration):

    dependencies = [
        ("users", "0006_alter_userpreference_value_json_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="usercomputed",
            name="computed_from_inferred_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="usercomputed",
            name="feature_logic_version",
            field=models.CharField(default="v1", max_length=32),
        ),
        migrations.AddField(
            model_name="userrawinferred",
            name="data_sufficiency_tier",
            field=core.security.encrypted_fields.EncryptedCharField(blank=True, default="", max_length=255),
        ),
        migrations.AddField(
            model_name="userrawinferred",
            name="feature_logic_version",
            field=models.CharField(default="v1", max_length=32),
        ),
        migrations.AddField(
            model_name="userrawinferred",
            name="sparse_signals_json",
            field=core.security.encrypted_fields.EncryptedJSONField(blank=True, default=dict),
        ),
    ]
