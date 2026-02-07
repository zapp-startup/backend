from django.urls import path
from . import views

app_name = "subscriptions"

urlpatterns = [
    path("manual/", views.subscription_manual_view, name="subscription_manual"),
    path("render/", views.subscription_render_view, name="subscription_render"),
    path("base/", views.SubscriptionBaseView.as_view(), name="subscription_base"),
    path("list/", views.SubscriptionListView.as_view(), name="subscription_list"),
]