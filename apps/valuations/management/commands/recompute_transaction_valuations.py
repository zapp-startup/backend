from django.core.management.base import BaseCommand

from apps.transactions.models import Transaction, TransactionDirection
from apps.transactions.services.significance import is_significant_transaction
from apps.valuations.services.transaction_value_score import persist_transaction_value_score
from apps.valuations.services.value_score_inference import ValueScoreModelNotAvailable


class Command(BaseCommand):
    help = "Recompute transaction valuations for one user or all users."

    def add_arguments(self, parser):
        parser.add_argument("--user-id", type=int, default=None)

    def handle(self, *args, **options):
        qs = Transaction.objects.filter(direction=TransactionDirection.SPEND).order_by("user_id", "-occurred_at")
        user_id = options["user_id"]
        if user_id is not None:
            qs = qs.filter(user_id=user_id)
        count = 0
        for txn in qs.iterator():
            if not is_significant_transaction(txn):
                continue
            try:
                persist_transaction_value_score(txn)
            except ValueScoreModelNotAvailable:
                continue
            count += 1
        self.stdout.write(self.style.SUCCESS(f"Recomputed {count} transaction valuation(s)."))
