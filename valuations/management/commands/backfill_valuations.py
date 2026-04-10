from django.core.management.base import BaseCommand

from users.services.state_orchestrator import recompute_user_state
from valuations.services.value_score_orchestrator import run_value_scores_for_user


class Command(BaseCommand):
    help = "Backfill user state and subscription valuations for all users."

    def handle(self, *args, **options):
        from django.contrib.auth import get_user_model

        User = get_user_model()
        count = 0
        for user in User.objects.all().order_by("id").iterator():
            recompute_user_state(user.id)
            run_value_scores_for_user(user.id)
            count += 1
        self.stdout.write(self.style.SUCCESS(f"Backfilled valuations for {count} user(s)."))
