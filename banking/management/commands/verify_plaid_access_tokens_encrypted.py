from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from banking.models import BankConnection
from banking.token_storage import has_plaid_token_encryption_enabled, is_plaid_access_token_encrypted


class Command(BaseCommand):
    help = "Fail if any stored Plaid access token remains plaintext."

    def handle(self, *args, **options):
        if not has_plaid_token_encryption_enabled():
            raise CommandError(
                "PLAID_TOKEN_ENCRYPTION_KEY or PLAID_TOKEN_ENCRYPTION_KEYS must be configured before verification."
            )

        plaintext_rows = []
        for connection in BankConnection.objects.only("id", "plaid_access_token").iterator():
            if not is_plaid_access_token_encrypted(connection.plaid_access_token):
                plaintext_rows.append(connection.id)

        if plaintext_rows:
            sample = ", ".join(str(item) for item in plaintext_rows[:10])
            raise CommandError(
                f"Found {len(plaintext_rows)} Plaid connections with plaintext access tokens. Sample ids: {sample}"
            )

        self.stdout.write(self.style.SUCCESS("All stored Plaid access tokens are encrypted."))
