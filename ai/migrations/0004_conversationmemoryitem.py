from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("ai", "0003_conversation_memory_fields"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="ConversationMemoryItem",
            fields=[
                ("id", models.BigAutoField(primary_key=True, serialize=False)),
                (
                    "memory_kind",
                    models.CharField(
                        choices=[
                            ("topic", "Topic"),
                            ("decision", "Decision"),
                            ("fact", "Fact"),
                            ("goal", "Goal"),
                            ("preference", "Preference"),
                        ],
                        default="topic",
                        max_length=32,
                    ),
                ),
                (
                    "dedupe_key",
                    models.CharField(
                        blank=True,
                        help_text="Optional stable key used to upsert durable memories.",
                        max_length=255,
                        null=True,
                    ),
                ),
                (
                    "summary_text",
                    models.TextField(
                        help_text="Short retrieval-friendly summary of the memory.",
                    ),
                ),
                (
                    "detail_json",
                    models.JSONField(
                        blank=True,
                        default=dict,
                        help_text="Optional structured details extracted from the turn.",
                    ),
                ),
                (
                    "tags_json",
                    models.JSONField(
                        blank=True,
                        default=list,
                        help_text="Normalized tags used for lightweight retrieval.",
                    ),
                ),
                (
                    "importance",
                    models.PositiveSmallIntegerField(
                        default=1,
                        help_text="Relative salience from 1 (low) to 5 (high).",
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "conversation",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="memory_items",
                        to="ai.conversation",
                    ),
                ),
                (
                    "source_message",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="memory_items",
                        to="ai.message",
                    ),
                ),
                (
                    "user",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="conversation_memory_items",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "indexes": [
                    models.Index(fields=["user", "updated_at"], name="ai_conversa_user_id_a20ca5_idx"),
                    models.Index(fields=["user", "memory_kind"], name="ai_conversa_user_id_27ae94_idx"),
                    models.Index(fields=["conversation", "updated_at"], name="ai_conversa_convers_1a03db_idx"),
                ],
                "constraints": [
                    models.UniqueConstraint(
                        condition=models.Q(("dedupe_key__isnull", False)),
                        fields=("user", "dedupe_key"),
                        name="uniq_user_conversation_memory_dedupe_key",
                    )
                ],
            },
        ),
    ]
