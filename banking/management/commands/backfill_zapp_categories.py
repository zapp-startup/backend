"""
Backfill Zapp categorization for existing BankTransactions.
Run after deploying the categorization system to categorize historical transactions.
"""
from django.core.management.base import BaseCommand

from banking.models import BankTransaction
from banking.services.categorization_service import run_categorization_on_transaction


class Command(BaseCommand):
    help = (
        "Backfill zapp_primary_category, zapp_subcategory on existing BankTransactions. "
        "Re-run anytime to apply improved PFC/legacy mappings and merchant rules."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Print what would be done without saving",
        )
        parser.add_argument(
            "--limit",
            type=int,
            default=0,
            help="Limit number of transactions to process (0 = all)",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        limit = options["limit"]

        qs = BankTransaction.objects.filter(removed=False).order_by("-date")
        if limit:
            qs = qs[:limit]

        total = qs.count()
        self.stdout.write(f"Processing {total} transactions...")

        updated = 0
        skipped = 0
        for txn in qs.iterator(chunk_size=500):
            if txn.user_override_category:
                skipped += 1
                continue
            if not dry_run:
                run_categorization_on_transaction(txn)
            updated += 1
            if updated % 500 == 0 and updated > 0:
                self.stdout.write(f"  Processed {updated}...")

        self.stdout.write(
            self.style.SUCCESS(
                f"Done. Updated {updated}, skipped (user override) {skipped}"
                + (" (dry run)" if dry_run else "")
            )
        )
