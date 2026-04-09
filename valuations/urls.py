from django.urls import path
from rest_framework.routers import DefaultRouter

from .value_score_views import ValueScoreMeView, ValueScoreRecomputeView
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

urlpatterns = [
    path("value-scores/me/", ValueScoreMeView.as_view(), name="value-scores-me"),
    path("value-scores/recompute/", ValueScoreRecomputeView.as_view(), name="value-scores-recompute"),
] + router.urls
