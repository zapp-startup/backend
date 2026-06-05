"""
Clear synthetic feedback and valuation data without deleting users or transactions.
Useful when regenerating feedback/valuations after fixing the generator.
"""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db import transaction

from apps.transactions.models import Transaction
from apps.valuations.models import SubscriptionValuation, ItemValuation
from apps.subscriptions.models import Subscription

User = get_user_model()


class Command(BaseCommand):
    help = "Clear synthetic feedback and valuation data (optional: by prefix or username)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--prefix", type=str, default=None,
            help="Only affect users whose username starts with this prefix.",
        )
        parser.add_argument(
            "--username", type=str, default=None,
            help="Target a single user by exact username (mutually exclusive with --prefix).",
        )
        parser.add_argument(
            "--feedback-only", action="store_true",
            help="Only set feedback_value_score and feedback_confidence to null.",
        )
        parser.add_argument(
            "--valuations-only", action="store_true",
            help="Only delete ItemValuations and SubscriptionValuations.",
        )
        parser.add_argument(
            "--all", action="store_true",
            help="Apply both feedback reset and valuations removal (default if no scope flag).",
        )

    def handle(self, *args, **opts):
        prefix = opts["prefix"]
        username = opts["username"]
        feedback_only = opts["feedback_only"]
        valuations_only = opts["valuations_only"]
        all_ops = opts["all"]

        if prefix and username:
            self.stderr.write(self.style.ERROR("Cannot use both --prefix and --username."))
            return
        if not prefix and not username:
            prefix = "seed_"

        if prefix:
            users = list(User.objects.filter(username__startswith=prefix))
        else:
            users = list(User.objects.filter(username=username))

        if not users:
            self.stdout.write(self.style.WARNING(f"No users found for prefix={prefix!r} or username={username!r}"))
            return

        user_ids = [u.pk for u in users]
        scope = f"prefix={prefix!r}" if prefix else f"username={username!r}"
        self.stdout.write(f"Clearing synthetic data for {len(users)} user(s) ({scope})...")

        do_feedback = feedback_only or (all_ops and not valuations_only)
        do_valuations = valuations_only or (all_ops and not feedback_only)

        if not do_feedback and not do_valuations:
            do_feedback = True
            do_valuations = True

        with transaction.atomic():
            if do_feedback:
                txns_updated = Transaction.objects.filter(user_id__in=user_ids).update(
                    feedback_value_score=None, feedback_confidence=None
                )
                subs_updated = Subscription.objects.filter(user_id__in=user_ids).update(
                    feedback_value_score=None, feedback_confidence=None
                )
                self.stdout.write(
                    self.style.SUCCESS(f"  Feedback cleared: {txns_updated} transactions, {subs_updated} subscriptions")
                )
            if do_valuations:
                sub_vals_deleted, _ = SubscriptionValuation.objects.filter(user_id__in=user_ids).delete()
                item_vals_deleted, _ = ItemValuation.objects.filter(user_id__in=user_ids).delete()
                self.stdout.write(
                    self.style.SUCCESS(
                        f"  Valuations removed: {sub_vals_deleted} subscription valuations, "
                        f"{item_vals_deleted} item valuations"
                    )
                )

        self.stdout.write(self.style.SUCCESS("Done."))
