from django.core.management.base import BaseCommand

from apps.users.services.state_orchestrator import recompute_user_state


class Command(BaseCommand):
    help = "Recompute UserRawInferred and UserComputed for one user or all users."

    def add_arguments(self, parser):
        parser.add_argument("--user-id", type=int, default=None)

    def handle(self, *args, **options):
        from django.contrib.auth import get_user_model

        User = get_user_model()
        qs = User.objects.all().order_by("id")
        user_id = options["user_id"]
        if user_id is not None:
            qs = qs.filter(id=user_id)
        count = 0
        for user in qs.iterator():
            recompute_user_state(user.id)
            count += 1
        self.stdout.write(self.style.SUCCESS(f"Recomputed user state for {count} user(s)."))
