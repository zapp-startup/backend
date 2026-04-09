from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("banking", "0008_alter_bankaccount_available_balance_and_more"),
        ("transactions", "0008_remove_transaction_valid_satisfaction_rating_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="transaction",
            name="bank_transaction",
            field=models.OneToOneField(
                blank=True,
                help_text="Linked bank-synced transaction when this ledger entry mirrors a synced purchase.",
                null=True,
                on_delete=models.SET_NULL,
                related_name="feedback_transaction",
                to="banking.banktransaction",
            ),
        ),
    ]
