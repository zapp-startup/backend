"""
Delete a single user by exact username.
Removes all user-related data in the correct cascade order.
"""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db import transaction

from ai.models import Conversation, Message, UserFact
from transactions.models import Transaction
from valuations.models import SubscriptionValuation, ItemValuation
from subscriptions.models import Subscription
from users.models import (
    UserPreference,
    UserRawExplicit,
    UserRawInferred,
    UserComputed,
)

User = get_user_model()


class Command(BaseCommand):
    help = "Delete a single user by exact username. Removes all related data."

    def add_arguments(self, parser):
        parser.add_argument("--username", type=str, required=True, help="Exact username to delete")
        parser.add_argument(
            "--force",
            action="store_true",
            help="Skip confirmation prompt (for non-interactive use)",
        )

    def handle(self, *args, **opts):
        username = opts["username"]
        force = opts["force"]

        user = User.objects.filter(username=username).first()
        if not user:
            self.stdout.write(self.style.ERROR(f"User '{username}' not found."))
            return

        if not force:
            confirm = input(f"Delete user '{username}' and ALL their data? [y/N]: ")
            if confirm.lower() != "y":
                self.stdout.write("Aborted.")
                return

        with transaction.atomic():
            user_id = user.pk

            # Cascade order (same as wipe_seed)
            Message.objects.filter(conversation__user_id=user_id).delete()
            Conversation.objects.filter(user_id=user_id).delete()
            UserFact.objects.filter(user_id=user_id).delete()

            Transaction.objects.filter(user_id=user_id).delete()
            SubscriptionValuation.objects.filter(user_id=user_id).delete()
            ItemValuation.objects.filter(user_id=user_id).delete()

            Subscription.objects.filter(user_id=user_id).delete()
            UserPreference.objects.filter(user_id=user_id).delete()

            UserComputed.objects.filter(user_id=user_id).delete()
            UserRawInferred.objects.filter(user_id=user_id).delete()
            UserRawExplicit.objects.filter(user_id=user_id).delete()

            user.delete()

        self.stdout.write(self.style.SUCCESS(f"Deleted user '{username}' and all related data."))
