from django.urls import path
from rest_framework.routers import DefaultRouter

from .value_score_views import (
    TransactionValuationListView,
    TransactionValuationRecomputeView,
    ValueScoreMeView,
    ValueScoreRecomputeView,
)
from .views import (
    ValuationModelVersionViewSet,
    SubscriptionValuationViewSet,
    ItemValuationViewSet,
    TransactionValuationViewSet,
)

router = DefaultRouter()
router.include_format_suffixes = False
router.register(r"valuation-model-versions", ValuationModelVersionViewSet, basename="valuation-model-versions")
router.register(r"subscription-valuations", SubscriptionValuationViewSet, basename="subscription-valuations")
router.register(r"item-valuations", ItemValuationViewSet, basename="item-valuations")
router.register(r"transaction-valuations", TransactionValuationViewSet, basename="transaction-valuations")

urlpatterns = [
    path("value-scores/me/", ValueScoreMeView.as_view(), name="value-scores-me"),
    path("value-scores/recompute/", ValueScoreRecomputeView.as_view(), name="value-scores-recompute"),
    path("transaction-valuations/latest/", TransactionValuationListView.as_view(), name="transaction-valuations-latest"),
    path("transaction-valuations/recompute/", TransactionValuationRecomputeView.as_view(), name="transaction-valuations-recompute"),
] + router.urls
