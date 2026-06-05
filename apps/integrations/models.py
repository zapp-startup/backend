from django.conf import settings
from django.db import models


class SpotifySyncStatus(models.TextChoices):
    """Connection: idle/syncing/ok/error. UI treats pending as loading — avoid pending on connection default."""

    IDLE = "idle", "Idle"
    SYNCING = "syncing", "Syncing"
    OK = "ok", "OK"
    ERROR = "error", "Error"
    PENDING = "pending", "Pending"  # legacy / sync-run queued


class SpotifyPayloadType(models.TextChoices):
    PROFILE = "profile", "Profile"
    RECENTLY_PLAYED = "recently_played", "Recently played"
    PLAYER = "player", "Playback state"
    CURRENTLY_PLAYING = "currently_playing", "Currently playing"
    TOP_TRACKS = "top_tracks", "Top tracks"
    TOP_ARTISTS = "top_artists", "Top artists"
    SAVED_TRACKS = "saved_tracks", "Saved tracks"


class SpotifyConnection(models.Model):
    """OAuth connection to Spotify Web API (one per Zapp user)."""

    id = models.BigAutoField(primary_key=True)
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="spotify_connection",
    )

    spotify_user_id = models.CharField(max_length=64, blank=True, db_index=True)
    spotify_uri = models.CharField(max_length=128, blank=True)
    display_name = models.CharField(max_length=255, blank=True)
    email = models.EmailField(blank=True, null=True)
    country = models.CharField(max_length=8, blank=True)
    product = models.CharField(
        max_length=32,
        blank=True,
        help_text="Spotify product / account type (e.g. premium, free).",
    )
    granted_scopes = models.TextField(blank=True)

    access_token_encrypted = models.TextField(blank=True)
    refresh_token_encrypted = models.TextField(blank=True)
    token_expires_at = models.DateTimeField(blank=True, null=True)

    last_synced_at = models.DateTimeField(blank=True, null=True)
    sync_status = models.CharField(
        max_length=16,
        choices=SpotifySyncStatus.choices,
        default=SpotifySyncStatus.IDLE,
    )
    last_error = models.TextField(blank=True, null=True)

    raw_profile_json = models.JSONField(blank=True, null=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=["spotify_user_id"]),
            models.Index(fields=["sync_status"]),
        ]

    def __str__(self) -> str:
        return f"SpotifyConnection({self.user_id})"


class SpotifySyncRun(models.Model):
    """One sync run (debugging / recompute)."""

    id = models.BigAutoField(primary_key=True)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="spotify_sync_runs",
    )
    connection = models.ForeignKey(
        SpotifyConnection,
        on_delete=models.CASCADE,
        related_name="sync_runs",
    )
    started_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(blank=True, null=True)
    status = models.CharField(
        max_length=16,
        choices=SpotifySyncStatus.choices,
        default=SpotifySyncStatus.PENDING,
    )
    error_message = models.TextField(blank=True, null=True)

    class Meta:
        indexes = [
            models.Index(fields=["user", "-started_at"]),
        ]


class SpotifyRawPayload(models.Model):
    """Raw API payloads for debugging, retraining, and recomputation."""

    id = models.BigAutoField(primary_key=True)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="spotify_raw_payloads",
    )
    sync_run = models.ForeignKey(
        SpotifySyncRun,
        on_delete=models.CASCADE,
        related_name="raw_payloads",
    )
    payload_type = models.CharField(
        max_length=32,
        choices=SpotifyPayloadType.choices,
    )
    raw_json = models.JSONField()
    fetched_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=["user", "payload_type", "-fetched_at"]),
        ]


class SpotifyPlayEvent(models.Model):
    """
    Normalized play events from recently-played (deduped) for 30d rollups.
    """

    id = models.BigAutoField(primary_key=True)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="spotify_play_events",
    )
    sync_run = models.ForeignKey(
        SpotifySyncRun,
        on_delete=models.CASCADE,
        related_name="play_events",
    )
    played_at = models.DateTimeField(db_index=True)
    track_id = models.CharField(max_length=64, db_index=True)
    track_uri = models.CharField(max_length=128, blank=True)
    track_name = models.CharField(max_length=512, blank=True)
    artist_name = models.CharField(max_length=512, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["user", "track_id", "played_at"],
                name="uniq_spotify_play_user_track_time",
            ),
        ]
        indexes = [
            models.Index(fields=["user", "-played_at"]),
        ]


class SpotifyFeatureSnapshot(models.Model):
    """
    Rich Spotify-derived features for subscription intelligence.
    Rolled-up Subscription fields are updated separately from this snapshot.
    """

    FEATURE_VERSION = 1

    id = models.BigAutoField(primary_key=True)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="spotify_feature_snapshots",
    )
    subscription = models.ForeignKey(
        "subscriptions.Subscription",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="spotify_feature_snapshots",
    )
    sync_run = models.ForeignKey(
        SpotifySyncRun,
        on_delete=models.CASCADE,
        related_name="feature_snapshots",
    )
    feature_version = models.PositiveSmallIntegerField(default=FEATURE_VERSION)
    computed_at = models.DateTimeField(auto_now_add=True)

    canonical_category = models.CharField(
        max_length=64,
        default="entertainment.streaming_music",
        db_index=True,
    )

    # JSON: all engineered metrics + metadata
    features = models.JSONField()

    class Meta:
        indexes = [
            models.Index(fields=["user", "-computed_at"]),
            models.Index(fields=["canonical_category", "-computed_at"]),
        ]
