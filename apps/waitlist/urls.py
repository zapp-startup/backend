from django.urls import path

from .views import WaitlistSignupView


urlpatterns = [
    path("waitlist-signups/", WaitlistSignupView.as_view(), name="waitlist-signups"),
]
