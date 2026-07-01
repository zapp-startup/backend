"""
URL configuration for the backend project.

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
    #path('subscriptions/', include('apps.subscriptions.urls')),

    path("api/", include("apps.subscriptions.urls")),
    path("api/", include("apps.transactions.urls")),
    path("api/", include("apps.users.urls")),
    path("api/", include("apps.valuations.urls")),
    path("api/ai/", include("apps.ai.urls")),
    path("api/", include("apps.banking.urls")),
    path("api/", include("apps.compliance.urls")),
    path("api/", include("apps.waitlist.urls")),
    path("api/gamification/", include("apps.gamification.urls")),
    path("api/", include("apps.integrations.urls")),
]
