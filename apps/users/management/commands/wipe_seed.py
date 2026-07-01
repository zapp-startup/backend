from __future__ import annotations

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db import connection, transaction

from apps.ai.models import Conversation, Message, UserFact
from apps.transactions.models import Transaction, TransactionReflection
from apps.valuations.models import SubscriptionValuation, ItemValuation
from apps.subscriptions.models import Subscription
from apps.users.models import (
    UserPreference,
    UserRawExplicit,
    UserRawInferred,
    UserComputed,
)

User = get_user_model()


class Command(BaseCommand):
    help = "Wipe seeded data ONLY (prefix-based). Safe for shared DB."

    @staticmethod
    def _table_exists(model) -> bool:
        return model._meta.db_table in connection.introspection.table_names()

    def add_arguments(self, parser):
        parser.add_argument("--prefix", type=str, default="seed_")
        parser.add_argument(
            "--size",
            type=int,
            default=None,
            help="Optional max number of seeded users to delete, ordered by id.",
        )

    @transaction.atomic
    def handle(self, *args, **opts):
        prefix = opts["prefix"]
        size = opts["size"]

        users = User.objects.filter(username__startswith=prefix).order_by("id")
        if size is not None:
            users = users[:size]
        user_ids = list(users.values_list("id", flat=True))
        users_to_delete = User.objects.filter(id__in=user_ids)

        # Delete children first
        Message.objects.filter(conversation__user_id__in=user_ids).delete()
        Conversation.objects.filter(user_id__in=user_ids).delete()
        UserFact.objects.filter(user_id__in=user_ids).delete()

        if self._table_exists(TransactionReflection):
            TransactionReflection.objects.filter(user_id__in=user_ids).delete()
        transaction_qs = Transaction.objects.filter(user_id__in=user_ids)
        transaction_qs._raw_delete(transaction_qs.db)
        SubscriptionValuation.objects.filter(user_id__in=user_ids).delete()
        ItemValuation.objects.filter(user_id__in=user_ids).delete()

        Subscription.objects.filter(user_id__in=user_ids).delete()
        UserPreference.objects.filter(user_id__in=user_ids).delete()

        # NEW 3-layer user tables
        UserComputed.objects.filter(user_id__in=user_ids).delete()
        UserRawInferred.objects.filter(user_id__in=user_ids).delete()
        UserRawExplicit.objects.filter(user_id__in=user_ids).delete()

        # Finally delete users
        deleted_count = len(user_ids)
        users_to_delete._raw_delete(users_to_delete.db)

        self.stdout.write(
            self.style.SUCCESS(f"Wiped seeded data ✅ users_deleted={deleted_count}, prefix='{prefix}'")
        )
