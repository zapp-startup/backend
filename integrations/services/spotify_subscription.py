from __future__ import annotations

from decimal import Decimal

from django.conf import settings

from subscriptions.models import (
    BillingCycle,
    Merchant,
    MerchantCategory,
    Subscription,
    SubscriptionEligibility,
    SubscriptionStatus,
)


def ensure_spotify_merchant() -> Merchant:
    merchant, _ = Merchant.objects.get_or_create(
        name="Spotify",
        defaults={
            "category": MerchantCategory.STREAMING,
            "website_domain": "spotify.com",
            "subscription_eligibility": SubscriptionEligibility.STANDARD_SUBSCRIPTION,
        },
    )
    return merchant


def resolve_spotify_subscription(user) -> Subscription | None:
    """
    Prefer an existing active Spotify subscription; optionally create one.
    """
    merchant = ensure_spotify_merchant()
    sub = (
        Subscription.objects.filter(
            user=user,
            merchant=merchant,
            status=SubscriptionStatus.ACTIVE,
        )
        .order_by("-updated_at")
        .first()
    )
    if sub:
        return sub

    if not getattr(settings, "SPOTIFY_AUTO_CREATE_SUBSCRIPTION", True):
        return None

    price = Decimal(str(getattr(settings, "SPOTIFY_DEFAULT_MONTHLY_PRICE_USD", "10.99")))
    return Subscription.objects.create(
        user=user,
        merchant=merchant,
        plan_name="Spotify",
        status=SubscriptionStatus.ACTIVE,
        billing_cycle=BillingCycle.MONTHLY,
        price=price,
        currency="USD",
        notes="Linked via Spotify integration; price may be estimated.",
    )


def apply_spotify_rollups(subscription: Subscription | None, rollup: dict) -> None:
    if subscription is None:
        return
    subscription.usage_frequency = rollup.get("usage_frequency")
    subscription.subscription_utilization = rollup.get("subscription_utilization")
    subscription.subscription_cost_benefit = rollup.get("subscription_cost_benefit")
    subscription.feedback_confidence = rollup.get("feedback_confidence")
    subscription.save(
        update_fields=[
            "usage_frequency",
            "subscription_utilization",
            "subscription_cost_benefit",
            "feedback_confidence",
            "updated_at",
        ]
    )
