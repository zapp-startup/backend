from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model
from django.db import transaction

from ai.models import Conversation, Message, UserFact
from transactions.models import Transaction
from valuations.models import SubscriptionValuation, ItemValuation
from subscriptions.models import Subscription
from users.models import UserProfile, UserPreference

User = get_user_model()

class Command(BaseCommand):
    help = "Wipe seeded data ONLY (prefix-based). Safe for shared DB."

    def add_arguments(self, parser):
        parser.add_argument("--prefix", type=str, default="seed_")

    @transaction.atomic
    def handle(self, *args, **opts):
        prefix = opts["prefix"]

        users = User.objects.filter(username__startswith=prefix)
        user_ids = list(users.values_list("id", flat=True))

        # Delete children first (safe even if some FKs are PROTECT/SET_NULL)
        Message.objects.filter(conversation__user_id__in=user_ids).delete()
        Conversation.objects.filter(user_id__in=user_ids).delete()
        UserFact.objects.filter(user_id__in=user_ids).delete()

        Transaction.objects.filter(user_id__in=user_ids).delete()
        SubscriptionValuation.objects.filter(user_id__in=user_ids).delete()
        ItemValuation.objects.filter(user_id__in=user_ids).delete()

        Subscription.objects.filter(user_id__in=user_ids).delete()
        UserPreference.objects.filter(user_id__in=user_ids).delete()
        UserProfile.objects.filter(user_id__in=user_ids).delete()

        # Finally delete users
        deleted_count = users.count()
        users.delete()

        self.stdout.write(self.style.SUCCESS(f"Wiped seeded data ✅ users_deleted={deleted_count}, prefix='{prefix}'"))
