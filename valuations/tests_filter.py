from datetime import date

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from subscriptions.models import BillingCycle, Merchant, MerchantCategory, Subscription, SubscriptionStatus
from valuations.models import SubscriptionValuation, ValuationModelVersion


User = get_user_model()


class SubscriptionValuationViewSetFilterTestCase(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="valuation-user",
            email="valuation@example.com",
            password="testpass123",
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

        self.model_version = ValuationModelVersion.objects.create(
            name="subscription_value",
            version="v-test",
        )
        self.merchant = Merchant.objects.create(
            name="Netflix",
            category=MerchantCategory.ENTERTAINMENT,
        )
        self.subscription_one = Subscription.objects.create(
            user=self.user,
            merchant=self.merchant,
            price=15.99,
            currency="USD",
            billing_cycle=BillingCycle.MONTHLY,
            status=SubscriptionStatus.ACTIVE,
            started_on=date(2025, 1, 1),
        )
        self.subscription_two = Subscription.objects.create(
            user=self.user,
            merchant=self.merchant,
            price=8.99,
            currency="USD",
            billing_cycle=BillingCycle.MONTHLY,
            status=SubscriptionStatus.ACTIVE,
            started_on=date(2025, 2, 1),
        )

        SubscriptionValuation.objects.create(
            user=self.user,
            subscription=self.subscription_one,
            model_version=self.model_version,
            period_start=date(2025, 3, 1),
            period_end=date(2025, 3, 31),
            total_cost="15.99",
            estimated_value="20.00",
            net_value="4.01",
            recommendation="buy",
            confidence=0.8,
        )
        SubscriptionValuation.objects.create(
            user=self.user,
            subscription=self.subscription_two,
            model_version=self.model_version,
            period_start=date(2025, 3, 1),
            period_end=date(2025, 3, 31),
            total_cost="8.99",
            estimated_value="6.00",
            net_value="-2.99",
            recommendation="wait",
            confidence=0.4,
        )

    def test_list_can_filter_by_subscription_id(self):
        response = self.client.get(
            f"/api/subscription-valuations/?subscription={self.subscription_one.id}"
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]["subscription"], self.subscription_one.id)

    def test_list_ignores_invalid_subscription_filter_values(self):
        response = self.client.get("/api/subscription-valuations/?subscription=abc")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 2)
