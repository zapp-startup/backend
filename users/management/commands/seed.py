from __future__ import annotations

import random
from datetime import timedelta, date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from faker import Faker

from users.models import (
    UserRawExplicit,
    UserRawInferred,
    UserComputed,
    UserPreference,
    FinancialGoal,
    RiskTolerance,
    BudgetStyle,
    PreferenceSource,
)
from subscriptions.models import Merchant, Subscription, MerchantCategory, BillingCycle, SubscriptionStatus
from transactions.models import Transaction, TransactionCategory, TransactionDirection, PaymentChannel
from valuations.models import (
    ValuationModelVersion,
    SubscriptionValuation,
    ItemValuation,
    Recommendation,
)
from ai.models import Conversation, Message, ConversationContext, MessageRole, UserFact

fake = Faker()
User = get_user_model()

SEED_PREFIX_DEFAULT = "seed_"


def rand_money(lo: float, hi: float) -> Decimal:
    return Decimal(str(round(random.uniform(lo, hi), 2)))


def pick(choices):
    return random.choice(list(choices))


class Command(BaseCommand):
    help = "Seed the DB with realistic fake data for users, onboarding/raw/computed, subs, txns, valuations, and AI chats."

    def add_arguments(self, parser):
        parser.add_argument("--users", type=int, default=50)
        parser.add_argument("--subs-per-user", type=int, default=8)
        parser.add_argument("--txns-per-user", type=int, default=200)
        parser.add_argument("--convos-per-user", type=int, default=3)
        parser.add_argument("--items-per-user", type=int, default=10)
        parser.add_argument("--months", type=int, default=12, help="How many months of history to generate.")
        parser.add_argument("--prefix", type=str, default=SEED_PREFIX_DEFAULT)
        parser.add_argument("--password", type=str, default="password123")

    @transaction.atomic
    def handle(self, *args, **opts):
        prefix: str = opts["prefix"]
        num_users: int = opts["users"]
        subs_per_user: int = opts["subs_per_user"]
        txns_per_user: int = opts["txns_per_user"]
        convos_per_user: int = opts["convos_per_user"]
        items_per_user: int = opts["items_per_user"]
        months: int = opts["months"]
        password: str = opts["password"]

        # 1) Ensure model versions exist
        sub_model, _ = ValuationModelVersion.objects.get_or_create(
            name="subscription_value",
            version="v1",
            defaults={"description": "Seed model version for subscription valuation."},
        )
        item_model, _ = ValuationModelVersion.objects.get_or_create(
            name="item_value",
            version="v1",
            defaults={"description": "Seed model version for item valuation."},
        )

        # 2) Create a reusable merchant catalog
        merchant_templates = [
            ("Netflix", MerchantCategory.STREAMING, "netflix.com"),
            ("Spotify", MerchantCategory.STREAMING, "spotify.com"),
            ("Hulu", MerchantCategory.STREAMING, "hulu.com"),
            ("YouTube Premium", MerchantCategory.STREAMING, "youtube.com"),
            ("Amazon Prime", MerchantCategory.SOFTWARE, "amazon.com"),
            ("Apple iCloud", MerchantCategory.SOFTWARE, "apple.com"),
            ("Google One", MerchantCategory.SOFTWARE, "google.com"),
            ("Planet Fitness", MerchantCategory.FITNESS, "planetfitness.com"),
            ("Xfinity", MerchantCategory.UTILITIES, "xfinity.com"),
            ("ComEd", MerchantCategory.UTILITIES, "comed.com"),
            ("DoorDash", MerchantCategory.FOOD, "doordash.com"),
            (
                "Uber One",
                MerchantCategory.TRANSPORT if hasattr(MerchantCategory, "TRANSPORT") else MerchantCategory.OTHER,
                "uber.com",
            ),
            ("Coursera", MerchantCategory.EDUCATION, "coursera.org"),
        ]

        merchants = []
        for name, cat, domain in merchant_templates:
            m, _ = Merchant.objects.get_or_create(
                name=name,
                defaults={"category": cat, "website_domain": domain},
            )
            merchants.append(m)

        # Add some random merchants too
        seed_domain_prefix = prefix.replace("_", "-").strip("-")  # domains can't have "_"

        for _ in range(40):
            base_name = fake.unique.company()
            name = f"{prefix}merchant_{base_name}"
            cat = pick(MerchantCategory)

            m, _ = Merchant.objects.get_or_create(
                name=name,
                defaults={
                    "category": cat,
                    "website_domain": f"{seed_domain_prefix}-{fake.domain_word()}.{fake.tld()}",
                },
            )
            merchants.append(m)

        # 3) Create users + all related data
        created_users = 0
        now = timezone.now()
        start_window = now - timedelta(days=30 * months)

        for i in range(num_users):
            username = f"{prefix}user_{i}"
            email = f"{username}@example.com"

            user, created = User.objects.get_or_create(
                username=username,
                defaults={
                    "email": email,
                    "first_name": fake.first_name(),
                    "last_name": fake.last_name(),
                },
            )
            if created:
                user.set_password(password)
                user.save()
                created_users += 1

            # 3a) NEW: Raw Explicit (survey/onboarding)
            raw_explicit, _ = UserRawExplicit.objects.get_or_create(
                user=user,
                defaults={
                    "display_name": f"{user.first_name} {user.last_name}",
                    "dob": fake.date_of_birth(minimum_age=18, maximum_age=55),
                    "age_range": random.choice(["18-24", "25-34", "35-44", "45+"]),
                    "household_size": random.choice([1, 2, 3, 4]),
                    "location_zip": fake.postcode(),
                    "life_stage": random.choice(["student", "early_career", "mid_career", "family", "other"]),
                    "employment_type": random.choice(["part_time", "full_time", "contract", "unemployed", "student"]),
                    "dependents_count": random.choice([0, 0, 0, 1, 2]),
                    "income_range": random.choice(["<25k", "25k-50k", "50k-100k", "100k+"]),
                    "monthly_income": rand_money(1500, 9000),
                    "monthly_fixed_expenses": rand_money(400, 4500),
                    "financial_goal": pick(FinancialGoal),
                    "risk_tolerance": pick(RiskTolerance),
                    "budget_style": pick(BudgetStyle),
                    "value_priority_cost": random.randint(20, 95),
                    "value_priority_quality": random.randint(20, 95),
                    "value_priority_sustainability": random.randint(0, 80),
                    "self_report_research_habit": random.randint(0, 100),
                },
            )

            # 3b) NEW: Raw Inferred snapshot (seed a plausible baseline)
            # You can tweak these ranges later to match your analytics pipeline.
            UserRawInferred.objects.update_or_create(
                user=user,
                defaults={
                    "window_days": 90,
                    "avg_purchase_price": rand_money(10, 90),
                    "purchase_price_variance": Decimal(str(round(random.uniform(50, 2500), 6))),
                    "category_distribution_json": {
                        "food": round(random.uniform(0.05, 0.25), 3),
                        "shopping": round(random.uniform(0.10, 0.35), 3),
                        "subscriptions": round(random.uniform(0.05, 0.20), 3),
                        "transport": round(random.uniform(0.02, 0.20), 3),
                    },
                    "percent_impulsive_purchases": round(random.uniform(0.05, 0.55), 3),
                    "regret_frequency": round(random.uniform(0.02, 0.30), 3),
                    "brand_repetition_rate": round(random.uniform(0.10, 0.80), 3),
                    "late_night_purchase_frequency": round(random.uniform(0.01, 0.25), 3),
                    "avg_decision_time_minutes": round(random.uniform(0.5, 15.0), 2),
                    "active_subscriptions_count": random.randint(0, subs_per_user),
                    "total_subscription_cost": rand_money(10, 120),
                    "percent_income_spent_on_subscriptions": round(random.uniform(0.005, 0.12), 4),
                    "subscription_usage_frequency_json": {
                        "streaming": random.choice(["daily", "weekly", "monthly"]),
                        "fitness": random.choice(["weekly", "monthly"]),
                    },
                    "cancel_reactivation_frequency": round(random.uniform(0.00, 0.20), 3),
                    "actual_monthly_spending": rand_money(800, 6500),
                },
            )

            # 3c) NEW: Computed (derive weights from explicit priorities, normalize to 0..1)
            # Handle missing values gracefully.
            c = raw_explicit.value_priority_cost or 0
            q = raw_explicit.value_priority_quality or 0
            s = raw_explicit.value_priority_sustainability or 0
            total = c + q + s

            if total > 0:
                cost_w = c / total
                qual_w = q / total
                sust_w = s / total
            else:
                cost_w = qual_w = sust_w = None

            UserComputed.objects.update_or_create(
                user=user,
                defaults={
                    "spending_personality": random.choice(
                        ["Value Hunter", "Convenience First", "Brand Loyalist", "Impulse Prone", "Balanced"]
                    ),
                    "product_spending_style": random.choice(["Planned", "Mixed", "Impulsive"]),
                    "subscription_behavior_type": random.choice(
                        ["Over-subscribed", "Churn-heavy", "Power-user", "Minimalist", "Bundle-driven"]
                    ),
                    "cost_weight": cost_w,
                    "quality_weight": qual_w,
                    "sustainability_weight": sust_w,
                    "impulse_susceptibility_score": round(random.uniform(0.05, 0.85), 3),
                    "regret_sensitivity": round(random.uniform(0.05, 0.75), 3),
                    "budget_adherence_score": round(random.uniform(0.10, 0.95), 3),
                },
            )

            # 3d) Preferences (KV table stays the same)
            pref_kvs = {
                "values_convenience": {
                    "value_type": "bool",
                    "value_json": {"value": random.choice([True, False])},
                },
                "subscription_sensitivity": {
                    "value_type": "int",
                    "value_json": {"value": random.randint(1, 10)},
                },
                "monthly_budget_target": {
                    "value_type": "float",
                    "value_json": {"value": float(rand_money(800, 4500))},
                },
            }
            for k, v in pref_kvs.items():
                UserPreference.objects.update_or_create(
                    user=user,
                    key=k,
                    defaults={
                        "value_type": v["value_type"],
                        "value_json": v["value_json"],
                        # No PreferenceSource.SEED in your new enum; use MANUAL or INFERRED.
                        "source": PreferenceSource.MANUAL,
                        "confidence": round(random.uniform(0.7, 1.0), 2),
                    },
                )

            # 3e) Facts (AI)
            UserFact.objects.update_or_create(
                user=user,
                fact_key="seed_tag",
                defaults={
                    "fact_value_json": {"seed": True, "prefix": prefix},
                    "source": "seed",
                    "confidence": Decimal("1.00"),
                },
            )

            # 3f) Subscriptions (respect unique active constraint)
            user_subs = []
            chosen_merchants = random.sample(merchants, k=min(subs_per_user, len(merchants)))

            for m in chosen_merchants:
                billing = pick(BillingCycle)
                price = (
                    rand_money(4.99, 24.99)
                    if billing == BillingCycle.MONTHLY
                    else rand_money(1.99, 9.99)
                    if billing == BillingCycle.WEEKLY
                    else rand_money(29.99, 199.99)
                    if billing == BillingCycle.YEARLY
                    else rand_money(5.00, 50.00)
                )

                status = random.choices(
                    [SubscriptionStatus.ACTIVE, SubscriptionStatus.PAUSED, SubscriptionStatus.CANCELED],
                    weights=[0.75, 0.1, 0.15],
                )[0]

                started_on = fake.date_between(start_date="-2y", end_date="today")
                renewal_date = None
                cancelled_on = None

                if status == SubscriptionStatus.ACTIVE:
                    renewal_date = date.today() + timedelta(days=random.randint(1, 30))
                elif status == SubscriptionStatus.CANCELED:
                    cancelled_on = fake.date_between(start_date=started_on, end_date="today")

                if Subscription.objects.filter(user=user, merchant=m, status=SubscriptionStatus.ACTIVE).exists():
                    if status == SubscriptionStatus.ACTIVE:
                        status = SubscriptionStatus.PAUSED

                sub = Subscription.objects.create(
                    user=user,
                    merchant=m,
                    plan_name=random.choice([None, "Basic", "Standard", "Premium", "Student"]),
                    status=status,
                    billing_cycle=billing,
                    price=price,
                    currency="USD",
                    started_on=started_on,
                    renewal_date=renewal_date,
                    cancelled_on=cancelled_on,
                    notes=random.choice([None, fake.sentence(nb_words=10)]),
                )
                user_subs.append(sub)

                # 3g) Subscription valuations
                end_date_ = date.today().replace(day=1)
                for k in range(months):
                    period_end = (end_date_ - timedelta(days=1)).replace(day=1) if k > 0 else date.today()
                    period_start = period_end.replace(day=1)

                    total_cost = price if billing == BillingCycle.MONTHLY else (
                        (price * Decimal("4.0")) if billing == BillingCycle.WEEKLY else
                        (price / Decimal("12.0")) if billing == BillingCycle.YEARLY else
                        price
                    )
                    est_value = total_cost * Decimal(str(round(random.uniform(0.6, 1.6), 2)))
                    net = est_value - total_cost

                    SubscriptionValuation.objects.create(
                        user=user,
                        subscription=sub,
                        model_version=sub_model,
                        period_start=period_start,
                        period_end=period_end,
                        total_cost=total_cost.quantize(Decimal("0.01")),
                        estimated_value=est_value.quantize(Decimal("0.01")),
                        net_value=net.quantize(Decimal("0.01")),
                        confidence=round(random.uniform(0.6, 0.95), 2),
                        explanation_json={
                            "drivers": random.sample(
                                ["usage_frequency", "alt_cost", "bundle_overlap", "price_sensitivity", "student_discount"],
                                k=3,
                            )
                        },
                    )

            # 3h) Transactions (some linked to subscriptions)
            for _ in range(txns_per_user):
                occurred = fake.date_time_between(
                    start_date=start_window,
                    end_date=now,
                    tzinfo=timezone.get_current_timezone(),
                )

                if user_subs and random.random() < 0.25:
                    sub = random.choice(user_subs)
                    merchant = sub.merchant
                    category = TransactionCategory.SUBSCRIPTIONS
                    direction = TransactionDirection.SPEND
                    amount = sub.price + rand_money(-1.00, 1.00)
                    if amount < 0:
                        amount = sub.price
                    subscription = sub
                else:
                    merchant = random.choice(merchants) if random.random() < 0.7 else None
                    subscription = None
                    category = pick(TransactionCategory)
                    direction = random.choices(
                        [TransactionDirection.SPEND, TransactionDirection.INCOME, TransactionDirection.REFUND],
                        weights=[0.78, 0.18, 0.04],
                    )[0]
                    amount = rand_money(5, 250) if direction != TransactionDirection.INCOME else rand_money(200, 5000)

                Transaction.objects.create(
                    user=user,
                    merchant=merchant,
                    subscription=subscription,
                    direction=direction,
                    amount=amount.copy_abs(),
                    currency="USD",
                    occurred_at=occurred,
                    category=category,
                    payment_channel=pick(PaymentChannel),
                    description_raw=fake.sentence(nb_words=6),
                )

            # 3i) Item valuations
            for _ in range(items_per_user):
                item_name = random.choice(
                    [
                        "AirPods", "Protein Powder", "iPad", "Textbook", "Running Shoes",
                        "Coffee Maker", "Office Chair", "Monitor", "Winter Jacket",
                    ]
                )
                fair_price = rand_money(20, 400)

                score = random.randint(1, 100)
                rec = Recommendation.BUY if score >= 75 else Recommendation.WAIT if score >= 45 else Recommendation.SKIP

                ItemValuation.objects.create(
                    user=user,
                    item_name=item_name,
                    item_category=random.choice(["electronics", "fitness", "education", "home", "other"]),
                    model_version=item_model,
                    estimated_fair_price=fair_price,
                    personal_value_score=score,
                    recommendation=rec,
                    reasoning_json={"signals": random.sample(["price", "need", "frequency", "alternatives", "budget_fit"], k=3)},
                )

            # 3j) Conversations + messages
            for _ in range(convos_per_user):
                context = pick(ConversationContext)
                linked_sub = random.choice(user_subs) if user_subs and random.random() < 0.4 else None

                convo = Conversation.objects.create(
                    user=user,
                    title=random.choice([None, fake.sentence(nb_words=4)]),
                    context_type=context,
                    linked_subscription=linked_sub,
                    linked_item_valuation=None,
                )

                Message.objects.create(conversation=convo, role=MessageRole.USER, content=fake.sentence(nb_words=10))
                Message.objects.create(conversation=convo, role=MessageRole.ASSISTANT, content=fake.sentence(nb_words=18))
                Message.objects.create(conversation=convo, role=MessageRole.USER, content=fake.sentence(nb_words=12))

        self.stdout.write(
            self.style.SUCCESS(f"Seed complete ✅ created_users={created_users}, prefix='{prefix}'")
        )