from django.contrib import admin

from integrations.models import (
    SpotifyConnection,
    SpotifyFeatureSnapshot,
    SpotifyPlayEvent,
    SpotifyRawPayload,
    SpotifySyncRun,
)


@admin.register(SpotifyConnection)
class SpotifyConnectionAdmin(admin.ModelAdmin):
    list_display = ("id", "user", "spotify_user_id", "display_name", "sync_status", "last_synced_at")
    search_fields = ("spotify_user_id", "display_name", "user__username")
    raw_id_fields = ("user",)


@admin.register(SpotifySyncRun)
class SpotifySyncRunAdmin(admin.ModelAdmin):
    list_display = ("id", "user", "status", "started_at", "finished_at")
    list_filter = ("status",)
    raw_id_fields = ("user", "connection")


@admin.register(SpotifyRawPayload)
class SpotifyRawPayloadAdmin(admin.ModelAdmin):
    list_display = ("id", "user", "payload_type", "fetched_at")
    list_filter = ("payload_type",)
    raw_id_fields = ("user", "sync_run")


@admin.register(SpotifyPlayEvent)
class SpotifyPlayEventAdmin(admin.ModelAdmin):
    list_display = ("id", "user", "track_id", "played_at")
    raw_id_fields = ("user", "sync_run")


@admin.register(SpotifyFeatureSnapshot)
class SpotifyFeatureSnapshotAdmin(admin.ModelAdmin):
    list_display = ("id", "user", "subscription", "computed_at", "feature_version")
    raw_id_fields = ("user", "subscription", "sync_run")
