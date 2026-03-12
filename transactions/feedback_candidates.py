"""
Feedback candidate selection for transactions.

Selects which transactions the app should ask the user for feedback about,
prioritizing high-signal discretionary purchases over routine expenses.
"""
import math
from datetime import timedelta
from decimal import Decimal
from typing import Optional

from django.db.models import Avg, Count, Q
from django.utils import timezone

from .models import Transaction, TransactionCategory


# --- Category weights (0.0 = exclude/heavily penalize, 1.0 = high signal) ---
# High-signal: restaurants, travel, shopping, entertainment, subscriptions
# Low-signal: groceries, rent, utilities, taxes, transfers, loan payments
CATEGORY_WEIGHTS = {
    TransactionCategory.SUBSCRIPTIONS: 0.95,
    TransactionCategory.EATING_OUT: 0.90,
    TransactionCategory.SHOPPING: 0.85,
    TransactionCategory.ENTERTAINMENT: 0.90,
    TransactionCategory.TRANSPORT: 0.50,  # mix: ride-share/travel vs routine gas
    TransactionCategory.HEALTH: 0.40,
    TransactionCategory.EDUCATION: 0.70,
    TransactionCategory.OTHER: 0.55,
    # Routine / low-signal (heavily penalized)
    TransactionCategory.GROCERIES: 0.10,
    TransactionCategory.BILLS: 0.05,
}

# Default weight for unknown categories
DEFAULT_CATEGORY_WEIGHT = 0.50

# Scoring coefficients (must sum to ~1.0; routine_penalty is subtracted)
WEIGHT_CATEGORY = 0.35
WEIGHT_AMOUNT = 0.25
WEIGHT_RECENCY = 0.20
WEIGHT_NOVELTY = 0.10
WEIGHT_UNCERTAINTY = 0.10
WEIGHT_ROUTINE_PENALTY = 0.25

# Time window for candidates (days)
DEFAULT_DAYS_WINDOW = 90

# Default number of candidates to return
DEFAULT_TOP_N = 2


def _has_feedback(t: Transaction) -> bool:
    """True if transaction already has any feedback."""
    return (
        t.satisfaction_rating is not None
        or t.regret_rating is not None
        or t.repurchase_likelihood is not None
        or (t.reflection_text and t.reflection_text.strip())
    )


def _category_weight(category: str) -> float:
    """Return 0.0-1.0 weight for category signal value."""
    return CATEGORY_WEIGHTS.get(category, DEFAULT_CATEGORY_WEIGHT)


def _amount_score(amount: Decimal, user_avg_amount: Optional[Decimal]) -> float:
    """
    Larger purchases provide more insight. Normalize relative to typical size.
    Returns 0.0-1.0; caps at 1.0 for very large purchases.
    """
    if amount <= 0:
        return 0.0
    if user_avg_amount is None or user_avg_amount <= 0:
        # No baseline: use log scale, treat $100 as "typical"
        log_amount = max(0, math.log(float(amount)) if amount > 0 else 0)
        # ln(10)~2.3, ln(100)~4.6, ln(1000)~6.9
        return min(1.0, log_amount / 7.0)
    ratio = float(amount) / float(user_avg_amount)
    return min(1.0, ratio / 3.0)  # 3x average = max score


def _recency_score(occurred_at, now=None) -> float:
    """
    Recent transactions are easier for users to remember.
    Returns 1.0 for today, decays to ~0 over 90 days.
    """
    now = now or timezone.now()
    od = occurred_at.date() if hasattr(occurred_at, "date") else occurred_at
    nd = now.date() if hasattr(now, "date") else now
    delta = nd - od
    days = delta.days
    if days <= 0:
        return 1.0
    # Exponential decay: 1.0 at 0 days, ~0.37 at 30 days, ~0.05 at 90 days
    return max(0.0, math.exp(-days / 30.0))


def _novelty_score(
    transaction: Transaction,
    feedback_counts_by_merchant: dict,
    feedback_counts_by_category: dict,
) -> float:
    """
    Prioritize merchants/categories where the user has little prior feedback.
    Returns 0.0-1.0; 1.0 = no prior feedback for this merchant or category.
    """
    merchant_id = transaction.merchant_id
    category = transaction.category or ""
    prior_merchant = feedback_counts_by_merchant.get(merchant_id, 0) if merchant_id else 0
    prior_category = feedback_counts_by_category.get(category, 0)
    # Combine: if we have little feedback for either, score is high
    total_prior = prior_merchant + prior_category
    if total_prior == 0:
        return 1.0
    return max(0.0, 1.0 - (total_prior / 5.0))  # 5+ prior = 0


def _uncertainty_score(transaction: Transaction) -> float:
    """
    Placeholder: if backend model confidence about this merchant/category is low.
    For now we use a simple heuristic: no merchant = higher uncertainty.
    """
    if transaction.merchant_id is None:
        return 0.6  # Unlinked merchant: we know less
    return 0.3  # Linked merchant: moderate uncertainty


def _routine_penalty(transaction: Transaction) -> float:
    """
    Recurring or predictable expenses. Returns 0.0-1.0; higher = more routine.
    """
    penalty = 0.0
    if transaction.subscription_id is not None:
        penalty += 0.8  # Known subscription = highly routine
    if transaction.category in (
        TransactionCategory.GROCERIES,
        TransactionCategory.BILLS,
    ):
        penalty += 0.5
    return min(1.0, penalty)


def compute_feedback_score(
    transaction: Transaction,
    user_avg_amount: Optional[Decimal],
    feedback_counts_by_merchant: dict,
    feedback_counts_by_category: dict,
    now=None,
) -> float:
    """
    Compute priority score for a transaction as a feedback candidate.
    Higher score = better candidate.
    """
    now = now or timezone.now()
    cat_w = _category_weight(transaction.category)
    amt_s = _amount_score(transaction.amount, user_avg_amount)
    rec_s = _recency_score(transaction.occurred_at, now)
    nov_s = _novelty_score(
        transaction, feedback_counts_by_merchant, feedback_counts_by_category
    )
    unc_s = _uncertainty_score(transaction)
    rtn_p = _routine_penalty(transaction)

    score = (
        WEIGHT_CATEGORY * cat_w
        + WEIGHT_AMOUNT * amt_s
        + WEIGHT_RECENCY * rec_s
        + WEIGHT_NOVELTY * nov_s
        + WEIGHT_UNCERTAINTY * unc_s
        - WEIGHT_ROUTINE_PENALTY * rtn_p
    )
    return max(0.0, score)


def _diversity_filter(
    scored: list[tuple[Transaction, float]],
    top_n: int,
) -> list[tuple[Transaction, float]]:
    """
    Apply diversity rule: avoid returning two nearly identical transactions
    (same merchant or same category cluster).
    """
    result = []
    used_merchants = set()
    used_categories = set()

    for t, s in scored:
        if len(result) >= top_n:
            break
        mid = t.merchant_id
        cat = t.category or ""
        # Skip if we already have one from same merchant (unless different category)
        if mid and mid in used_merchants:
            continue
        # Skip if we already have one from same category (allow 1 per category)
        if cat and cat in used_categories:
            continue
        result.append((t, s))
        if mid:
            used_merchants.add(mid)
        if cat:
            used_categories.add(cat)

    return result


def get_feedback_candidates(
    user,
    days_window: int = DEFAULT_DAYS_WINDOW,
    top_n: int = DEFAULT_TOP_N,
) -> list[dict]:
    """
    Return the top N transaction candidates for feedback.

    Filters:
    - Belongs to user
    - No existing feedback
    - Spend direction (not income/refund)
    - Within time window
    - Completed (no pending; Transaction has no pending field, so all are completed)

    Returns list of {"transaction_id": id} dicts.
    """
    now = timezone.now()
    cutoff = now - timedelta(days=days_window)

    qs = (
        Transaction.objects.filter(user=user)
        .filter(
            Q(satisfaction_rating__isnull=True)
            & Q(regret_rating__isnull=True)
            & Q(repurchase_likelihood__isnull=True)
            & (Q(reflection_text__isnull=True) | Q(reflection_text=""))
        )
        .filter(direction="spend")
        .filter(occurred_at__gte=cutoff)
        .select_related("merchant", "subscription")
        .order_by("-occurred_at")
    )

    # Exclude routine categories entirely (optional: could score them very low instead)
    low_signal_categories = [
        TransactionCategory.GROCERIES,
        TransactionCategory.BILLS,
    ]
    qs = qs.exclude(category__in=low_signal_categories)

    transactions = list(qs[:500])  # Cap for performance

    if not transactions:
        return []

    # User's average transaction amount (from transactions with feedback, or all)
    user_avg = (
        Transaction.objects.filter(user=user)
        .filter(direction="spend")
        .exclude(amount__lte=0)
        .aggregate(avg=Avg("amount"))["avg"]
    )

    # Prior feedback counts by merchant and category
    feedback_txns = Transaction.objects.filter(user=user).filter(
        Q(satisfaction_rating__isnull=False)
        | Q(regret_rating__isnull=False)
        | Q(repurchase_likelihood__isnull=False)
    )
    feedback_counts_by_merchant = dict(
        feedback_txns.exclude(merchant_id__isnull=True)
        .values("merchant_id")
        .annotate(c=Count("id"))
        .values_list("merchant_id", "c")
    )
    feedback_counts_by_category = dict(
        feedback_txns.exclude(category="")
        .values("category")
        .annotate(c=Count("id"))
        .values_list("category", "c")
    )

    # Score each transaction
    scored = []
    for t in transactions:
        if _has_feedback(t):
            continue
        s = compute_feedback_score(
            t, user_avg, feedback_counts_by_merchant, feedback_counts_by_category, now
        )
        scored.append((t, s))

    # Sort by score descending
    scored.sort(key=lambda x: x[1], reverse=True)

    # Apply diversity filter
    diverse = _diversity_filter(scored, top_n)

    return [{"transaction_id": t.id} for t, _ in diverse]
