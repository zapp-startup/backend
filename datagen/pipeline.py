"""
Pipeline orchestrator: runs agents in correct order per user (Section 23),
then persists all generated data to the database via bulk_create.
"""

from __future__ import annotations

import sys
from datetime import date, timedelta
from decimal import Decimal
from typing import Callable

from django.contrib.auth import get_user_model
from django.db import connection, transaction
from django.utils import timezone

from faker import Faker

from datagen.agents.anomaly import AnomalyAgent
from datagen.agents.audit import AuditAgent
from datagen.agents.behavior import BehaviorAgent
from datagen.agents.conversation import ConversationAgent
from datagen.agents.income import IncomeAgent
from datagen.agents.merchant import MerchantAgent
from datagen.agents.obligation import ObligationAgent
from datagen.agents.persona import PersonaAgent
from datagen.agents.spend import SpendAgent
from datagen.agents.subscription import SubscriptionAgent
from datagen.agents.valuation import ValuationAgent
from datagen.distributions import make_rng
from datagen.state import UserState

User = get_user_model()
fake = Faker()


def _raise_statement_timeout(seconds: int = 300) -> None:
    """Set higher statement_timeout for long-running seed. Postgres only."""
    try:
        with connection.cursor() as cursor:
            cursor.execute("SET statement_timeout = %s", [f"{seconds}s"])
    except Exception:
        pass  # Non-Postgres or missing privilege: leave default


def run_pipeline(
    num_users: int = 50,
    months: int = 18,
    prefix: str = "seed_",
    password: str = "password123",
    use_llm: bool = False,
    max_llm_reflections: int = 20,
    seed: int | None = None,
    log: Callable[[str], None] = lambda msg: print(msg),
) -> dict:
    """
    Main entry point: generate synthetic data for num_users users.
    Returns summary stats.
    """
    rng = make_rng(seed)

    # Raise statement_timeout for seed (avoids Postgres/Supabase default ~8s limit)
    _raise_statement_timeout(seconds=300)

    now = timezone.now()
    end_date = now.date()
    start_date = end_date - timedelta(days=30 * months)

    # Ensure model versions exist
    from valuations.models import ValuationModelVersion
    sub_model, _ = ValuationModelVersion.objects.get_or_create(
        name="subscription_value", version="v1",
        defaults={"description": "Agent-based synthetic data model version."},
    )
    item_model, _ = ValuationModelVersion.objects.get_or_create(
        name="item_value", version="v1",
        defaults={"description": "Agent-based synthetic data model version."},
    )

    # Build shared merchant catalog (Agent 3 runs once globally)
    merchant_agent = MerchantAgent(rng, use_llm)
    global_context = {
        "start_date": start_date,
        "end_date": end_date,
    }
    catalog_result = merchant_agent.run(UserState(), global_context)
    merchant_catalog = catalog_result["merchant_catalog"]

    # Persist merchants
    merchant_db_map = _persist_merchants(merchant_catalog)

    stats = {
        "users_created": 0,
        "transactions": 0,
        "subscriptions": 0,
        "sub_valuations": 0,
        "item_valuations": 0,
        "conversations": 0,
        "facts": 0,
        "anomalies": 0,
        "audit_repairs": 0,
    }

    for i in range(num_users):
        user_seed = int(rng.integers(0, 2**31))
        user_rng = make_rng(user_seed)

        with transaction.atomic():
            user_stats = _generate_one_user(
                idx=i,
                rng=user_rng,
                prefix=prefix,
                password=password,
                use_llm=use_llm,
                max_llm_reflections=max_llm_reflections,
                start_date=start_date,
                end_date=end_date,
                merchant_agent=MerchantAgent(user_rng, use_llm),
                merchant_catalog=merchant_catalog,
                merchant_db_map=merchant_db_map,
                sub_model=sub_model,
                item_model=item_model,
            )

        for k, v in user_stats.items():
            stats[k] = stats.get(k, 0) + v

        if (i + 1) % 10 == 0 or i == num_users - 1:
            log(f"  Generated user {i+1}/{num_users}")

    return stats


def _generate_one_user(
    idx: int,
    rng,
    prefix: str,
    password: str,
    use_llm: bool,
    max_llm_reflections: int,
    start_date: date,
    end_date: date,
    merchant_agent: MerchantAgent,
    merchant_catalog: list[dict],
    merchant_db_map: dict,
    sub_model,
    item_model,
) -> dict:
    """Generate all data for a single user following Section 23 order."""
    from ai.models import Conversation, Message, MessageRole, ConversationContext, UserFact
    from subscriptions.models import (
        Merchant, Subscription, SubscriptionStatus, BillingCycle, MerchantCategory,
    )
    from transactions.models import (
        Transaction, TransactionCategory, TransactionDirection, PaymentChannel,
    )
    from users.models import (
        UserComputed, UserPreference, UserRawExplicit, UserRawInferred,
        FinancialGoal, RiskTolerance, BudgetStyle, PreferenceSource,
    )
    from valuations.models import (
        ItemValuation, SubscriptionValuation, Recommendation, ValuationContext,
    )

    state = UserState()
    context = {
        "start_date": start_date,
        "end_date": end_date,
        "merchant_agent": merchant_agent,
        "merchant_catalog": merchant_catalog,
        "max_llm_reflections": max_llm_reflections,
    }

    stats = {
        "users_created": 0,
        "transactions": 0,
        "subscriptions": 0,
        "sub_valuations": 0,
        "item_valuations": 0,
        "conversations": 0,
        "facts": 0,
        "anomalies": 0,
        "audit_repairs": 0,
    }

    # === Step 1: Persona Agent ===
    persona = PersonaAgent(rng, use_llm)
    persona_result = persona.run(state, context)
    context.update(persona_result)

    # Create Django user
    username = f"{prefix}user_{idx}"
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
        stats["users_created"] = 1

    # Persist UserRawExplicit
    re_data = persona_result["raw_explicit_data"]
    display_name = f"{user.first_name} {user.last_name}"
    UserRawExplicit.objects.update_or_create(
        user=user,
        defaults={
            "display_name": display_name,
            **re_data,
        },
    )

    # Persist UserComputed
    UserComputed.objects.update_or_create(
        user=user,
        defaults=persona_result["computed_data"],
    )

    # Persist UserPreferences
    _persist_preferences(user, state, rng)

    # === Step 2: Income Agent ===
    income_agent = IncomeAgent(rng, use_llm)
    income_result = income_agent.run(state, context)
    context.update(income_result)

    # === Step 3: Subscription Agent ===
    sub_agent = SubscriptionAgent(rng, use_llm)
    sub_result = sub_agent.run(state, context)
    context.update(sub_result)

    # Persist subscriptions
    db_subs = _persist_subscriptions(user, sub_result["subscriptions"], merchant_db_map)
    stats["subscriptions"] = len(db_subs)
    sub_by_merchant = {s.merchant.name: s for s in db_subs}

    # Link subscription transactions to DB subs (O(1) lookup via dict)
    sub_txns = sub_result.get("subscription_transactions", [])
    for txn in sub_txns:
        merch_name = txn.get("merchant_info", {}).get("name", "") if txn.get("merchant_info") else ""
        db_sub = sub_by_merchant.get(merch_name)
        if db_sub:
            txn["subscription_obj"] = db_sub
            txn["merchant_obj"] = db_sub.merchant

    # === Step 4: Obligation Agent ===
    obligation_agent = ObligationAgent(rng, use_llm)
    obligation_result = obligation_agent.run(state, context)
    context.update(obligation_result)

    # === Step 5: Spend Agent ===
    spend_agent = SpendAgent(rng, use_llm)
    spend_result = spend_agent.run(state, context)
    context.update(spend_result)

    # === Step 6: Behavior Agent ===
    behavior_agent = BehaviorAgent(rng, use_llm)
    behavior_result = behavior_agent.run(state, context)
    context.update(behavior_result)

    # === Step 7: Anomaly Agent ===
    anomaly_agent = AnomalyAgent(rng, use_llm)
    anomaly_result = anomaly_agent.run(state, context)
    context.update(anomaly_result)
    stats["anomalies"] = len(anomaly_result.get("anomaly_transactions", []))

    # === Step 8: Valuation Agent ===
    valuation_agent = ValuationAgent(rng, use_llm)
    valuation_result = valuation_agent.run(state, context)
    context.update(valuation_result)

    # === Step 9: AI Conversation Agent ===
    convo_agent = ConversationAgent(rng, use_llm)
    convo_result = convo_agent.run(state, context)
    context.update(convo_result)

    # === Step 10: Audit Agent ===
    audit_agent = AuditAgent(rng, use_llm)
    audit_result = audit_agent.run(state, context)
    stats["audit_repairs"] = len(audit_result.get("audit_repairs", []))

    # === Persist all transactions ===
    all_txn_dicts = []
    for key in ("income_transactions", "subscription_transactions",
                "obligation_transactions", "spend_transactions",
                "anomaly_transactions"):
        all_txn_dicts.extend(context.get(key, []))

    db_txns = _persist_transactions(user, all_txn_dicts, merchant_db_map)
    stats["transactions"] = len(db_txns)

    # === Persist subscription valuations ===
    db_sub_vals = _persist_subscription_valuations(
        user, valuation_result.get("subscription_valuations", []),
        sub_by_merchant, sub_model,
    )
    stats["sub_valuations"] = len(db_sub_vals)

    # === Persist item valuations ===
    db_item_vals = _persist_item_valuations(
        user, valuation_result.get("item_valuations", []), item_model,
    )
    stats["item_valuations"] = len(db_item_vals)

    # === Persist conversations ===
    db_convos = _persist_conversations(
        user, convo_result.get("conversations", []),
        db_subs, db_item_vals,
    )
    stats["conversations"] = len(db_convos)

    # === Persist user facts ===
    all_facts = (
        behavior_result.get("behavior_facts", [])
        + convo_result.get("conversation_facts", [])
    )
    db_facts = _persist_facts(user, all_facts, prefix)
    stats["facts"] = len(db_facts)

    # === Compute UserRawInferred from actual transactions ===
    _compute_raw_inferred(user, db_txns, db_subs, state)

    # Monthly trait drift (Section 18.3)
    state.apply_trait_drift(rng)

    return stats


# =====================================================================
# Persistence helpers
# =====================================================================

def _persist_merchants(catalog: list[dict]) -> dict:
    """Ensure all catalog merchants exist in DB. Returns name->Merchant map."""
    from subscriptions.models import Merchant, MerchantCategory

    cat_map = {
        "streaming": MerchantCategory.STREAMING,
        "grocery": MerchantCategory.GROCERY,
        "fitness": MerchantCategory.FITNESS,
        "software": MerchantCategory.SOFTWARE,
        "utilities": MerchantCategory.UTILITIES,
        "food": MerchantCategory.FOOD,
        "education": MerchantCategory.EDUCATION,
        "other": MerchantCategory.OTHER,
    }

    db_map = {}
    for m in catalog:
        cat = cat_map.get(m["category"], MerchantCategory.OTHER)
        obj, _ = Merchant.objects.get_or_create(
            name=m["name"],
            defaults={"category": cat, "website_domain": m.get("domain", "")},
        )
        db_map[m["name"]] = obj

    return db_map


def _persist_subscriptions(user, sub_dicts: list[dict], merchant_db_map: dict):
    """Create Subscription DB rows via bulk_create. Returns list of DB objects."""
    from subscriptions.models import Subscription, SubscriptionStatus, BillingCycle

    status_map = {
        "active": SubscriptionStatus.ACTIVE,
        "paused": SubscriptionStatus.PAUSED,
        "canceled": SubscriptionStatus.CANCELED,
    }
    cycle_map = {
        "monthly": BillingCycle.MONTHLY,
        "yearly": BillingCycle.YEARLY,
        "weekly": BillingCycle.WEEKLY,
    }

    # Prefetch existing active subscriptions once (avoids N exists() queries)
    existing_active_merchant_ids = set(
        Subscription.objects.filter(
            user=user, status=SubscriptionStatus.ACTIVE
        ).values_list("merchant_id", flat=True)
    )
    used_merchants_active = set()
    to_create = []

    for sd in sub_dicts:
        merch_name = sd.get("merchant_info", {}).get("name", "")
        merchant = merchant_db_map.get(merch_name)
        if not merchant:
            continue

        status = status_map.get(sd.get("status", "active"), SubscriptionStatus.ACTIVE)

        # Respect unique active constraint (in-memory only)
        if status == SubscriptionStatus.ACTIVE:
            if merchant.pk in used_merchants_active or merchant.pk in existing_active_merchant_ids:
                status = SubscriptionStatus.PAUSED
            else:
                used_merchants_active.add(merchant.pk)

        billing = cycle_map.get(sd.get("billing_cycle", "monthly"), BillingCycle.MONTHLY)

        to_create.append(Subscription(
            user=user,
            merchant=merchant,
            plan_name=sd.get("plan_name"),
            status=status,
            billing_cycle=billing,
            price=sd.get("price", Decimal("9.99")),
            currency="USD",
            started_on=sd.get("started_on"),
            renewal_date=sd.get("renewal_date"),
            cancelled_on=sd.get("cancelled_on"),
            usage_frequency=sd.get("usage_frequency"),
            reactivation_count=sd.get("reactivation_count", 0),
        ))

    db_subs = []
    if to_create:
        db_subs = Subscription.objects.bulk_create(to_create, batch_size=100)
    return db_subs


def _persist_transactions(user, txn_dicts: list[dict], merchant_db_map: dict):
    """Bulk-create Transaction rows."""
    from transactions.models import (
        Transaction, TransactionDirection, TransactionCategory, PaymentChannel,
    )

    direction_map = {
        "spend": TransactionDirection.SPEND,
        "income": TransactionDirection.INCOME,
        "refund": TransactionDirection.REFUND,
    }
    category_map = {
        "subscriptions": TransactionCategory.SUBSCRIPTIONS,
        "groceries": TransactionCategory.GROCERIES,
        "eating_out": TransactionCategory.EATING_OUT,
        "transport": TransactionCategory.TRANSPORT,
        "shopping": TransactionCategory.SHOPPING,
        "bills": TransactionCategory.BILLS,
        "entertainment": TransactionCategory.ENTERTAINMENT,
        "health": TransactionCategory.HEALTH,
        "education": TransactionCategory.EDUCATION,
        "other": TransactionCategory.OTHER,
    }
    channel_map = {
        "card": PaymentChannel.CARD,
        "cash": PaymentChannel.CASH,
        "online": PaymentChannel.ONLINE,
        "bank": PaymentChannel.BANK,
        "other": PaymentChannel.OTHER,
    }

    objs = []
    for td in txn_dicts:
        merchant = td.get("merchant_obj")
        if not merchant:
            merch_info = td.get("merchant_info")
            if merch_info:
                merchant = merchant_db_map.get(merch_info.get("name"))

        subscription = td.get("subscription_obj")

        direction = direction_map.get(td.get("direction", "spend"), TransactionDirection.SPEND)
        category = category_map.get(td.get("category", "other"), TransactionCategory.OTHER)
        channel = channel_map.get(td.get("payment_channel", "card"), PaymentChannel.CARD)

        obj = Transaction(
            user=user,
            merchant=merchant,
            subscription=subscription,
            direction=direction,
            amount=abs(td["amount"]),
            currency="USD",
            occurred_at=td["occurred_at"],
            category=category,
            payment_channel=channel,
            description_raw=td.get("description_raw", ""),
            satisfaction_rating=td.get("satisfaction_rating"),
            regret_rating=td.get("regret_rating"),
            repurchase_likelihood=td.get("repurchase_likelihood"),
            usage_frequency=td.get("usage_frequency"),
            reflection_text=td.get("reflection_text"),
            considered_at=td.get("considered_at"),
            used_buy_advisor=td.get("used_buy_advisor", False),
            self_report_researched=td.get("self_report_researched"),
            impulse_score=td.get("impulse_score"),
            regret_score=td.get("regret_score"),
        )
        objs.append(obj)

    if objs:
        Transaction.objects.bulk_create(objs, batch_size=500)

    return objs


def _persist_subscription_valuations(user, val_dicts: list[dict],
                                      sub_by_merchant: dict, sub_model):
    """Create SubscriptionValuation rows (O(1) lookup via sub_by_merchant)."""
    from valuations.models import SubscriptionValuation, ValuationContext

    context_map = {
        "subscription_renewal": ValuationContext.SUBSCRIPTION_RENEWAL,
        "subscription_cancel": ValuationContext.SUBSCRIPTION_CANCEL,
    }

    objs = []
    for vd in val_dicts:
        sub_ref = vd.get("_sub_ref", {})
        merch_name = sub_ref.get("merchant_info", {}).get("name", "")
        db_sub = sub_by_merchant.get(merch_name)
        if not db_sub:
            continue

        ctx = context_map.get(vd.get("context", ""), ValuationContext.SUBSCRIPTION_RENEWAL)

        objs.append(SubscriptionValuation(
            user=user,
            subscription=db_sub,
            model_version=sub_model,
            context=ctx,
            period_start=vd["period_start"],
            period_end=vd["period_end"],
            total_cost=vd["total_cost"],
            estimated_value=vd["estimated_value"],
            net_value=vd["net_value"],
            recommendation=vd.get("recommendation", ""),
            confidence=vd.get("confidence", 0.7),
            evidence_json=vd.get("evidence_json", {}),
            explanation_json=vd.get("explanation_json", {}),
        ))

    if objs:
        SubscriptionValuation.objects.bulk_create(objs, batch_size=500, ignore_conflicts=True)

    return objs


def _persist_item_valuations(user, val_dicts: list[dict], item_model):
    """Create ItemValuation rows."""
    from valuations.models import ItemValuation, Recommendation, ValuationContext

    rec_map = {
        "buy": Recommendation.BUY,
        "wait": Recommendation.WAIT,
        "skip": Recommendation.SKIP,
        "alternative": Recommendation.ALTERNATIVE,
    }

    objs = []
    for vd in val_dicts:
        objs.append(ItemValuation(
            user=user,
            item_name=vd["item_name"],
            item_category=vd.get("item_category", ""),
            model_version=item_model,
            context=ValuationContext.ONE_OFF_PURCHASE,
            observed_price=vd.get("observed_price"),
            estimated_fair_price=vd.get("estimated_fair_price"),
            personal_value_score=vd["personal_value_score"],
            recommendation=rec_map.get(vd.get("recommendation", "wait"), Recommendation.WAIT),
            confidence=vd.get("confidence", 0.7),
            evidence_json=vd.get("evidence_json", {}),
            reasoning_json=vd.get("reasoning_json", {}),
        ))

    if objs:
        ItemValuation.objects.bulk_create(objs, batch_size=500)

    return objs


def _persist_conversations(user, convo_dicts: list[dict],
                            db_subs: list, db_item_vals: list):
    """Create Conversation and Message rows via bulk_create."""
    from ai.models import Conversation, Message, ConversationContext, MessageRole

    context_map = {
        "general": ConversationContext.GENERAL,
        "subscription": ConversationContext.SUBSCRIPTION,
        "product": ConversationContext.PRODUCT,
        "budgeting": ConversationContext.BUDGETING,
    }
    role_map = {
        "user": MessageRole.USER,
        "assistant": MessageRole.ASSISTANT,
        "system": MessageRole.SYSTEM,
    }

    # Build and bulk-create conversations (PostgreSQL returns PKs)
    convos_to_create = []
    for cd in convo_dicts:
        ctx = context_map.get(cd.get("context_type", "general"), ConversationContext.GENERAL)
        linked_sub = None
        if cd.get("linked_sub_idx") is not None and cd["linked_sub_idx"] < len(db_subs):
            linked_sub = db_subs[cd["linked_sub_idx"]]
        linked_item = None
        if cd.get("linked_item_idx") is not None and cd["linked_item_idx"] < len(db_item_vals):
            linked_item = db_item_vals[cd["linked_item_idx"]]

        convos_to_create.append(Conversation(
            user=user,
            title=cd.get("title"),
            context_type=ctx,
            linked_subscription=linked_sub,
            linked_item_valuation=linked_item,
        ))

    if not convos_to_create:
        return []

    db_convos = Conversation.objects.bulk_create(convos_to_create, batch_size=50)

    # Bulk-create all messages (conversations have PKs on PostgreSQL)
    all_messages = []
    for i, cd in enumerate(convo_dicts):
        convo = db_convos[i]
        for msg in cd.get("messages", []):
            all_messages.append(Message(
                conversation=convo,
                role=role_map.get(msg.get("role", "user"), MessageRole.USER),
                content=msg.get("content", ""),
            ))
    if all_messages:
        Message.objects.bulk_create(all_messages, batch_size=500)

    return db_convos


def _persist_facts(user, fact_dicts: list[dict], prefix: str):
    """Create/update UserFact rows via batched bulk_create and bulk_update."""
    from ai.models import UserFact

    # Prefetch existing facts once
    existing_by_key = {
        f.fact_key: f for f in UserFact.objects.filter(user=user)
    }

    # seed_tag + generated facts
    all_fact_data = [
        {
            "fact_key": "seed_tag",
            "fact_value_json": {"seed": True, "prefix": prefix},
            "source": "seed",
            "confidence": Decimal("1.00"),
        },
    ]
    all_fact_data.extend([
        {
            "fact_key": fd["fact_key"],
            "fact_value_json": fd.get("fact_value_json", {}),
            "source": fd.get("source", "inferred"),
            "confidence": Decimal(str(fd.get("confidence", 0.80))),
        }
        for fd in fact_dicts
    ])

    to_create = []
    to_update = []

    for fd in all_fact_data:
        key = fd["fact_key"]
        existing = existing_by_key.get(key)
        if existing:
            existing.fact_value_json = fd["fact_value_json"]
            existing.source = fd["source"]
            existing.confidence = fd["confidence"]
            to_update.append(existing)
        else:
            to_create.append(UserFact(
                user=user,
                fact_key=key,
                fact_value_json=fd["fact_value_json"],
                source=fd["source"],
                confidence=fd["confidence"],
            ))

    if to_create:
        UserFact.objects.bulk_create(to_create, batch_size=100)
    if to_update:
        UserFact.objects.bulk_update(
            to_update, ["fact_value_json", "source", "confidence"], batch_size=100
        )

    return fact_dicts


def _persist_preferences(user, state: UserState, rng):
    """Create/update UserPreference KV rows via batched bulk_create and bulk_update."""
    from users.models import UserPreference, PreferenceSource

    prefs = {
        "values_convenience": {
            "value_type": "bool",
            "value_json": {"value": state.quality_preference < 0.4},
        },
        "subscription_sensitivity": {
            "value_type": "int",
            "value_json": {"value": int(state.credit_stress * 10)},
        },
        "monthly_budget_target": {
            "value_type": "float",
            "value_json": {"value": float(state.monthly_income) * (0.7 + state.budget_adherence * 0.2)},
        },
    }

    # Prefetch existing preferences once
    existing_by_key = {
        p.key: p for p in UserPreference.objects.filter(user=user)
    }

    to_create = []
    to_update = []

    for k, v in prefs.items():
        conf = round(0.7 + float(rng.random()) * 0.3, 2)
        existing = existing_by_key.get(k)
        if existing:
            existing.value_type = v["value_type"]
            existing.value_json = v["value_json"]
            existing.confidence = conf
            to_update.append(existing)
        else:
            to_create.append(UserPreference(
                user=user,
                key=k,
                value_type=v["value_type"],
                value_json=v["value_json"],
                source=PreferenceSource.MANUAL,
                confidence=conf,
            ))

    if to_create:
        UserPreference.objects.bulk_create(to_create, batch_size=10)
    if to_update:
        UserPreference.objects.bulk_update(
            to_update, ["value_type", "value_json", "confidence"], batch_size=10
        )


def _compute_raw_inferred(user, db_txns: list, db_subs: list, state: UserState):
    """
    Compute UserRawInferred from actual generated transactions.
    Section 22 requirement: inferred metrics must agree with generated transactions.
    """
    from users.models import UserRawInferred

    spend_txns = [t for t in db_txns if t.direction == "spend"]
    if not spend_txns:
        UserRawInferred.objects.update_or_create(
            user=user,
            defaults={"window_days": 90},
        )
        return

    amounts = [float(t.amount) for t in spend_txns]
    avg_price = sum(amounts) / len(amounts)
    variance = sum((a - avg_price) ** 2 for a in amounts) / max(1, len(amounts) - 1)

    # Category distribution
    cat_counts: dict[str, int] = {}
    total_count = len(spend_txns)
    for t in spend_txns:
        cat_counts[t.category] = cat_counts.get(t.category, 0) + 1
    cat_dist = {k: round(v / total_count, 3) for k, v in cat_counts.items()}

    # Impulse / regret metrics
    impulse_scores = [t.impulse_score for t in spend_txns if t.impulse_score is not None]
    regret_scores = [t.regret_score for t in spend_txns if t.regret_score is not None]

    pct_impulse = (
        sum(1 for s in impulse_scores if s > 0.5) / max(1, len(impulse_scores))
        if impulse_scores else None
    )
    regret_freq = (
        sum(1 for s in regret_scores if s > 0.4) / max(1, len(regret_scores))
        if regret_scores else None
    )

    # Late night
    late_night = sum(1 for t in spend_txns if t.occurred_at.hour >= 22 or t.occurred_at.hour < 5)
    late_night_freq = late_night / total_count if total_count > 0 else None

    # Brand repetition
    merchant_counts: dict[str, int] = {}
    for t in spend_txns:
        if t.merchant_id:
            key = str(t.merchant_id)
            merchant_counts[key] = merchant_counts.get(key, 0) + 1
    repeat_count = sum(1 for v in merchant_counts.values() if v > 1)
    brand_rep = repeat_count / max(1, len(merchant_counts)) if merchant_counts else None

    # Subscription aggregates
    active_subs = [s for s in db_subs if s.status == "active"]
    total_sub_cost = sum(float(s.price) for s in active_subs)
    monthly_income = max(1, float(state.monthly_income))

    # Decision time (from considered_at)
    decision_times = []
    for t in spend_txns:
        if t.considered_at and t.occurred_at:
            diff = (t.occurred_at - t.considered_at).total_seconds() / 60.0
            if 0 < diff < 10000:
                decision_times.append(diff)
    avg_decision = sum(decision_times) / len(decision_times) if decision_times else None

    total_spend = sum(amounts)
    months_in_window = max(1, 3)  # 90-day window
    actual_monthly = total_spend / months_in_window

    # Subscription usage frequency
    sub_usage = {}
    for s in active_subs:
        cat = s.merchant.category if s.merchant else "other"
        freq = s.usage_frequency
        if freq is not None:
            if freq >= 5:
                sub_usage[cat] = "daily"
            elif freq >= 2:
                sub_usage[cat] = "weekly"
            else:
                sub_usage[cat] = "monthly"

    cancel_react = 0
    for s in db_subs:
        if s.reactivation_count > 0:
            cancel_react += 1
    cancel_react_freq = cancel_react / max(1, len(db_subs)) if db_subs else None

    UserRawInferred.objects.update_or_create(
        user=user,
        defaults={
            "window_days": 90,
            "avg_purchase_price": Decimal(str(round(avg_price, 2))),
            "purchase_price_variance": Decimal(str(round(variance, 6))),
            "category_distribution_json": cat_dist,
            "percent_impulsive_purchases": round(pct_impulse, 3) if pct_impulse is not None else None,
            "regret_frequency": round(regret_freq, 3) if regret_freq is not None else None,
            "brand_repetition_rate": round(brand_rep, 3) if brand_rep is not None else None,
            "late_night_purchase_frequency": round(late_night_freq, 3) if late_night_freq is not None else None,
            "avg_decision_time_minutes": round(avg_decision, 2) if avg_decision is not None else None,
            "active_subscriptions_count": len(active_subs),
            "total_subscription_cost": Decimal(str(round(total_sub_cost, 2))),
            "percent_income_spent_on_subscriptions": round(
                total_sub_cost / monthly_income, 4
            ) if monthly_income > 0 else None,
            "subscription_usage_frequency_json": sub_usage,
            "cancel_reactivation_frequency": round(cancel_react_freq, 3) if cancel_react_freq is not None else None,
            "actual_monthly_spending": Decimal(str(round(actual_monthly, 2))),
        },
    )
