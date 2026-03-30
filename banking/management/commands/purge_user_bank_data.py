"""
Management command: purge Plaid-linked bank data for a user (e.g. account closure).

Usage:
  python manage.py purge_user_bank_data --user-id 42
"""
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from banking.lifecycle import purge_user_bank_data

User = get_user_model()


class Command(BaseCommand):
    help = "Delete all bank connections, accounts, and Plaid transactions for a user."

    def add_arguments(self, parser):
        parser.add_argument("--user-id", type=int, required=True, help="Django user primary key")

    def handle(self, *args, **options):
        uid = options["user_id"]
        try:
            user = User.objects.get(pk=uid)
        except User.DoesNotExist as exc:
            raise CommandError(f"User id={uid} not found") from exc

        result = purge_user_bank_data(user)
        self.stdout.write(self.style.SUCCESS(str(result)))
