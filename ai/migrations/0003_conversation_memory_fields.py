from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("ai", "0002_alter_conversation_id_alter_message_id_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="conversation",
            name="last_summarized_message_id",
            field=models.BigIntegerField(
                blank=True,
                help_text="Highest message id included in the rolling summary.",
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="conversation",
            name="session_state_json",
            field=models.JSONField(
                blank=True,
                default=dict,
                help_text="Structured short-term memory for active goals, entities, and open loops.",
            ),
        ),
        migrations.AddField(
            model_name="conversation",
            name="summary_text",
            field=models.TextField(
                blank=True,
                default="",
                help_text="Rolling summary of older turns kept in the short-term memory store.",
            ),
        ),
    ]
