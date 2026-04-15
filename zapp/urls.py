"""
URL configuration for zapp project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/6.0/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.contrib import admin
from django.http import JsonResponse
from django.urls import path, include


def healthcheck(_request):
    return JsonResponse({"ok": True, "service": "zapp-backend"})


urlpatterns = [
    path('admin/', admin.site.urls),
    path("healthz/", healthcheck),
    #path('subscriptions/', include('subscriptions.urls')),

    path("api/", include("subscriptions.urls")),
    path("api/", include("transactions.urls")),
    path("api/", include("users.urls")),
    path("api/", include("valuations.urls")),
    path("api/ai/", include("ai.urls")),
    path("api/", include("banking.urls")),
    path("api/", include("compliance.urls")),
    path("api/", include("waitlist.urls")),
    path("api/gamification/", include("gamification.urls")),
    path("api/", include("integrations.urls")),
]
