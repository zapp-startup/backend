from django.core.management.base import BaseCommand

from apps.integrations.models import SpotifyConnection
from apps.integrations.services.spotify_sync import sync_spotify_for_user


class Command(BaseCommand):
    help = "Recompute Spotify subscription features from live API (same as sync)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--user-id",
            type=int,
            help="Optional: limit to one user.",
        )

    def handle(self, *args, **options):
        uid = options.get("user_id")
        qs = SpotifyConnection.objects.exclude(refresh_token_encrypted="").exclude(
            refresh_token_encrypted__isnull=True
        )
        if uid:
            qs = qs.filter(user_id=uid)

        for conn in qs.iterator():
            try:
                sync_spotify_for_user(conn.user)
                self.stdout.write(self.style.SUCCESS(f"OK user_id={conn.user_id}"))
            except Exception as exc:
                self.stdout.write(self.style.ERROR(f"FAIL user_id={conn.user_id}: {exc}"))
