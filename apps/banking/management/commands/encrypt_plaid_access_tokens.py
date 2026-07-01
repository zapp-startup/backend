"""
Backfill app-layer encryption for stored Plaid access tokens.

Usage:
  python manage.py encrypt_plaid_access_tokens
  python manage.py encrypt_plaid_access_tokens --dry-run
"""
from django.core.management.base import BaseCommand, CommandError

from apps.banking.models import BankConnection
from apps.banking.token_storage import (
    ensure_plaid_access_token_encrypted,
    has_plaid_token_encryption_enabled,
    is_plaid_access_token_encrypted,
)


class Command(BaseCommand):
    help = "Encrypt legacy plaintext Plaid access tokens using PLAID_TOKEN_ENCRYPTION_KEY."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report how many connections still have plaintext Plaid access tokens.",
        )

    def handle(self, *args, **options):
        if not has_plaid_token_encryption_enabled():
            raise CommandError(
                "PLAID_TOKEN_ENCRYPTION_KEY must be configured before encrypting Plaid access tokens."
            )

        dry_run = options["dry_run"]
        inspected = 0
        plaintext_rows = 0
        upgraded = 0

        for connection in BankConnection.objects.only("id", "plaid_access_token"):
            inspected += 1
            if is_plaid_access_token_encrypted(connection.plaid_access_token):
                continue
            plaintext_rows += 1
            if not dry_run and ensure_plaid_access_token_encrypted(connection):
                upgraded += 1

        if dry_run:
            self.stdout.write(
                self.style.WARNING(
                    f"Dry run: {plaintext_rows} of {inspected} Plaid connections still store plaintext access tokens."
                )
            )
            return

        self.stdout.write(
            self.style.SUCCESS(
                f"Encrypted {upgraded} Plaid access tokens ({plaintext_rows} plaintext rows found, {inspected} connections inspected)."
            )
        )
