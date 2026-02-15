from django.urls import include, path
from rest_framework.routers import DefaultRouter
from .views import MerchantViewSet, SubscriptionViewSet

router = DefaultRouter()
router.include_format_suffixes = False
router.register(r"merchants", MerchantViewSet, basename="merchants")
router.register(r"subscriptions", SubscriptionViewSet, basename="subscriptions")

urlpatterns = [
    path("", include(router.urls)),
]
