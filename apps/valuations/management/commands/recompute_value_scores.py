from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand

from apps.valuations.services.value_score_orchestrator import run_value_scores_for_user


class Command(BaseCommand):
    help = "Run value score model for a user (requires trained checkpoint under VALUE_SCORE_CHECKPOINT_DIR)."

    def add_arguments(self, parser):
        parser.add_argument("--user-id", type=int, required=True)
        parser.add_argument(
            "--subscription-id",
            type=int,
            action="append",
            dest="subscription_ids",
            help="Limit to specific subscription id (repeatable).",
        )

    def handle(self, *args, **options):
        uid = options["user_id"]
        sub_ids = options.get("subscription_ids") or None
        User = get_user_model()
        if not User.objects.filter(pk=uid).exists():
            self.stderr.write(self.style.ERROR(f"User {uid} not found."))
            return
        out = run_value_scores_for_user(uid, subscription_ids=sub_ids)
        if not out.get("ok"):
            self.stderr.write(self.style.ERROR(out.get("error", "failed")))
            return
        n = len(out.get("valuations") or [])
        self.stdout.write(self.style.SUCCESS(f"Wrote {n} subscription valuation(s). {out.get('model_version', '')}"))
