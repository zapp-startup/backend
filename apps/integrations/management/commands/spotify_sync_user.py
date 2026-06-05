from django.core.management.base import BaseCommand, CommandError

from apps.integrations.services.spotify_sync import sync_spotify_for_user
from apps.users.models import User


class Command(BaseCommand):
    help = "Sync Spotify read-only data for a single user (by user id or username)."

    def add_arguments(self, parser):
        parser.add_argument("--user-id", type=int, help="User primary key")
        parser.add_argument("--username", type=str, help="User username")

    def handle(self, *args, **options):
        uid = options.get("user_id")
        username = options.get("username")
        if not uid and not username:
            raise CommandError("Provide --user-id or --username")

        if uid:
            user = User.objects.filter(pk=uid).first()
        else:
            user = User.objects.filter(username=username).first()

        if not user:
            raise CommandError("User not found")

        snap = sync_spotify_for_user(user)
        self.stdout.write(self.style.SUCCESS(f"Snapshot {snap.pk} created"))
