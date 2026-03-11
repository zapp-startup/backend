from rest_framework.routers import DefaultRouter
from .views import (
    ValuationModelVersionViewSet,
    SubscriptionValuationViewSet,
    ItemValuationViewSet,
)

router = DefaultRouter()
router.include_format_suffixes = False
router.register(r"valuation-model-versions", ValuationModelVersionViewSet, basename="valuation-model-versions")
router.register(r"subscription-valuations", SubscriptionValuationViewSet, basename="subscription-valuations")
router.register(r"item-valuations", ItemValuationViewSet, basename="item-valuations")

urlpatterns = router.urls
