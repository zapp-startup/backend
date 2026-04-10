from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion

import zapp.security.encrypted_fields


class Migration(migrations.Migration):

    dependencies = [
        ("transactions", "0011_encrypt_persisted_value_score_fields"),
        ("subscriptions", "0009_remove_subscription_valid_subscription_feedback_value_score_and_more"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("valuations", "0009_alter_itemvaluation_estimated_fair_price_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="subscriptionvaluation",
            name="base_value_score",
            field=models.PositiveSmallIntegerField(blank=True, help_text="0-150 baseline score before tier/context adjustments.", null=True),
        ),
        migrations.AddField(
            model_name="subscriptionvaluation",
            name="feature_window_days",
            field=models.PositiveSmallIntegerField(default=30),
        ),
        migrations.AddField(
            model_name="subscriptionvaluation",
            name="inference_status",
            field=models.CharField(blank=True, default="success", max_length=32),
        ),
        migrations.AddField(
            model_name="subscriptionvaluation",
            name="tier_used",
            field=models.CharField(blank=True, default="", max_length=32),
        ),
        migrations.AddConstraint(
            model_name="subscriptionvaluation",
            constraint=models.CheckConstraint(condition=models.Q(("base_value_score__gte", 0), ("base_value_score__lte", 150)) | models.Q(("base_value_score__isnull", True)), name="valid_subscription_base_value_score"),
        ),
        migrations.CreateModel(
            name="TransactionValuation",
            fields=[
                ("id", models.BigAutoField(primary_key=True, serialize=False)),
                ("context", models.CharField(choices=[("subscription_renewal", "Subscription Renewal"), ("subscription_cancel", "Subscription Cancel"), ("one_off_purchase", "One-off Purchase"), ("upgrade", "Upgrade"), ("other", "Other")], default="one_off_purchase", max_length=32)),
                ("value_score", models.PositiveSmallIntegerField()),
                ("base_value_score", models.PositiveSmallIntegerField(blank=True, null=True)),
                ("confidence", models.FloatField(default=1.0)),
                ("tier_used", models.CharField(blank=True, default="", max_length=32)),
                ("inference_status", models.CharField(blank=True, default="success", max_length=32)),
                ("stale_at", models.DateTimeField(blank=True, null=True)),
                ("evidence_json", zapp.security.encrypted_fields.EncryptedJSONField(blank=True, default=dict)),
                ("reasoning_json", zapp.security.encrypted_fields.EncryptedJSONField(blank=True, default=dict)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("model_version", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="transaction_valuations", to="valuations.valuationmodelversion")),
                ("transaction", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="valuations", to="transactions.transaction")),
                ("user", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="transaction_valuations", to=settings.AUTH_USER_MODEL)),
            ],
            options={
                "ordering": ["-created_at"],
            },
        ),
        migrations.AddIndex(
            model_name="transactionvaluation",
            index=models.Index(fields=["user", "created_at"], name="valuations_t_user_id_5d358e_idx"),
        ),
        migrations.AddIndex(
            model_name="transactionvaluation",
            index=models.Index(fields=["transaction", "created_at"], name="valuations_t_transac_61d822_idx"),
        ),
        migrations.AddIndex(
            model_name="transactionvaluation",
            index=models.Index(fields=["model_version", "created_at"], name="valuations_t_model_v_27a80e_idx"),
        ),
        migrations.AddConstraint(
            model_name="transactionvaluation",
            constraint=models.CheckConstraint(condition=models.Q(("value_score__gte", 0), ("value_score__lte", 150)), name="valid_transaction_value_score"),
        ),
        migrations.AddConstraint(
            model_name="transactionvaluation",
            constraint=models.CheckConstraint(condition=models.Q(("base_value_score__gte", 0), ("base_value_score__lte", 150)) | models.Q(("base_value_score__isnull", True)), name="valid_transaction_base_value_score"),
        ),
        migrations.AddConstraint(
            model_name="transactionvaluation",
            constraint=models.CheckConstraint(condition=models.Q(("confidence__gte", 0), ("confidence__lte", 1)), name="valid_transaction_confidence"),
        ),
        migrations.CreateModel(
            name="ValuationDirtyState",
            fields=[
                ("id", models.BigAutoField(primary_key=True, serialize=False)),
                ("dirty_reason", models.CharField(max_length=64)),
                ("priority", models.PositiveSmallIntegerField(default=5)),
                ("dirty_since", models.DateTimeField(auto_now_add=True)),
                ("attempt_count", models.PositiveIntegerField(default=0)),
                ("last_error_code", models.CharField(blank=True, default="", max_length=64)),
                ("subscription", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name="dirty_states", to="subscriptions.subscription")),
                ("transaction", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name="dirty_states", to="transactions.transaction")),
                ("user", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="valuation_dirty_states", to=settings.AUTH_USER_MODEL)),
            ],
        ),
        migrations.AddIndex(
            model_name="valuationdirtystate",
            index=models.Index(fields=["priority", "dirty_since"], name="valuations_v_priority_17c7c8_idx"),
        ),
        migrations.AddIndex(
            model_name="valuationdirtystate",
            index=models.Index(fields=["user", "dirty_since"], name="valuations_v_user_id_733629_idx"),
        ),
    ]
