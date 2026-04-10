"""Spotify Web API OAuth + category constants."""

SPOTIFY_AUTH_BASE = "https://accounts.spotify.com"
SPOTIFY_API_BASE = "https://api.spotify.com/v1"

SPOTIFY_SCOPES = " ".join(
    [
        "user-read-recently-played",
        "user-read-playback-state",
        "user-read-currently-playing",
        "user-top-read",
        "user-library-read",
        "user-read-private",
    ]
)

CANONICAL_CATEGORY = "entertainment.streaming_music"
