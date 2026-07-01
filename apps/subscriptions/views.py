from rest_framework.viewsets import ModelViewSet, ReadOnlyModelViewSet

from apps.gamification.services import (
    award_points_for_subscription_added,
    award_points_for_subscription_cancelled,
    award_points_for_subscription_paused,
)
from apps.subscriptions.services.events import mark_subscription_dirty

from apps.valuations.value_score_views import annotate_subscriptions_with_latest_score
from apps.valuations.services.value_score_orchestrator import schedule_value_scores_for_user_after_commit

from apps.users.permissions import IsFullyAuthenticated

from .models import Merchant, Subscription
from .serializers import MerchantSerializer, SubscriptionSerializer


class MerchantViewSet(ReadOnlyModelViewSet):
    """
    Merchants are canonical/global, read-only for normal users. The catalog is
    only consumed from inside the authenticated dashboard, so it inherits the
    project-wide IsAuthenticated default rather than being publicly exposed.
    """
    queryset = Merchant.objects.all()
    serializer_class = MerchantSerializer


class SubscriptionViewSet(ModelViewSet):
    serializer_class = SubscriptionSerializer
    permission_classes = [IsFullyAuthenticated]

    def get_queryset(self):
        # Users can only see their own subscriptions
        qs = Subscription.objects.filter(user=self.request.user).select_related("merchant")
        return annotate_subscriptions_with_latest_score(qs)

    def perform_create(self, serializer):
        # Force ownership
        subscription = serializer.save(user=self.request.user)
        award_points_for_subscription_added(subscription)
        mark_subscription_dirty(subscription, reason="subscription_created", priority=3)
        schedule_value_scores_for_user_after_commit(
            self.request.user.id, subscription_ids=[subscription.id]
        )

    def perform_update(self, serializer):
        previous = self.get_object()
        previous_status = previous.status
        subscription = serializer.save(user=self.request.user)
        mark_subscription_dirty(subscription, reason="subscription_updated", priority=3)
        schedule_value_scores_for_user_after_commit(
            self.request.user.id, subscription_ids=[subscription.id]
        )

        if previous_status != subscription.status:
            if subscription.status == "canceled":
                award_points_for_subscription_cancelled(subscription)
            elif subscription.status == "paused":
                award_points_for_subscription_paused(subscription)
