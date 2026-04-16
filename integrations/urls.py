from django.urls import path

from integrations import views

urlpatterns = [
    path("integrations/spotify/connect/", views.SpotifyConnectView.as_view(), name="spotify-connect"),
    path("integrations/spotify/callback/", views.SpotifyCallbackView.as_view(), name="spotify-callback"),
    path("integrations/spotify/insights/", views.SpotifyInsightsView.as_view(), name="spotify-insights"),
    path("integrations/spotify/status/", views.SpotifyStatusView.as_view(), name="spotify-status"),
    path("integrations/spotify/sync/", views.SpotifySyncView.as_view(), name="spotify-sync"),
    path("integrations/spotify/disconnect/", views.SpotifyDisconnectView.as_view(), name="spotify-disconnect"),
]
