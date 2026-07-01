from django.core.management.base import BaseCommand

from apps.integrations.models import SpotifyConnection
from apps.integrations.services.spotify_sync import sync_spotify_for_user


class Command(BaseCommand):
    help = "Backfill sync for all users with a Spotify refresh token."

    def handle(self, *args, **options):
        qs = SpotifyConnection.objects.exclude(refresh_token_encrypted="").exclude(
            refresh_token_encrypted__isnull=True
        )
        for conn in qs.iterator():
            try:
                sync_spotify_for_user(conn.user)
                self.stdout.write(self.style.SUCCESS(f"OK user_id={conn.user_id}"))
            except Exception as exc:
                self.stdout.write(self.style.ERROR(f"FAIL user_id={conn.user_id}: {exc}"))
