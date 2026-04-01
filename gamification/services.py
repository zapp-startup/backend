from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Count, Q, Sum
from django.utils import timezone

from subscriptions.models import Subscription, SubscriptionStatus
from transactions.feedback_candidates import get_feedback_candidates
from transactions.models import Transaction, TransactionCategory, TransactionReflection
from .badges import ACTION_POINTS, BADGE_CATALOG
from .models import (
    Badge,
    Group,
    GroupMember,
    MonthlyTarget,
    MonthlyTargetStatus,
    PeriodicReview,
    PeriodicReviewStatus,
    PeriodicReviewType,
    PointAction,
    PointEvent,
    UserBadge,
    UserStreak,
)

User = get_user_model()

STREAK_QUALIFYING_ACTIONS = {
    PointAction.LOG_PURCHASE,
    PointAction.REFLECT_SAME_DAY,
    PointAction.USE_ADVISOR,
}

LEVEL_BASE_POINTS = 50
LEVEL_STEP_POINTS = 25
LOW_SIGNAL_REVIEW_CATEGORIES = {
    TransactionCategory.GROCERIES,
    TransactionCategory.BILLS,
}
REVIEW_SUMMARY_REQUIREMENTS = {
    PeriodicReviewType.WEEKLY: ("wins", "regrets", "adjustment"),
    PeriodicReviewType.MONTHLY: ("best_purchase", "most_regretted_purchase", "next_month_focus"),
}
REVIEW_MINIMUM_TRANSACTION_FEEDBACK = {
    PeriodicReviewType.WEEKLY: 1,
    PeriodicReviewType.MONTHLY: 2,
}
REVIEW_TRANSACTION_CANDIDATE_LIMIT = {
    PeriodicReviewType.WEEKLY: 3,
    PeriodicReviewType.MONTHLY: 5,
}
SUBSCRIPTION_RENEWAL_NUDGE_LIMIT = 3
LOW_VALUE_SUBSCRIPTION_NUDGE_LIMIT = 3


@dataclass(frozen=True)
class AwardResult:
    event: PointEvent
    created: bool
    badges_awarded: list[UserBadge]


def sync_badge_catalog() -> None:
    for badge in BADGE_CATALOG:
        Badge.objects.update_or_create(
            code=badge["code"],
            defaults={
                "name": badge["name"],
                "description": badge["description"],
                "icon": badge["icon"],
                "category": badge["category"],
                "is_active": True,
            },
        )


def build_weekly_review_event_key(user: User, window_start: date) -> str:
    return f"weekly_review:user:{user.id}:{window_start.isoformat()}"


def build_monthly_review_event_key(user: User, month_key: str) -> str:
    return f"monthly_review:user:{user.id}:{month_key}"


def build_complete_monthly_target_event_key(user: User, target_type: str, month_key: str) -> str:
    return f"complete_monthly_target:user:{user.id}:{target_type}:{month_key}"


def _normalize_event_key(event_key: str | None) -> str | None:
    if event_key is None:
        return None
    event_key = event_key.strip()
    return event_key or None


def normalize_week_start(window_date: date) -> date:
    return window_date - timedelta(days=window_date.weekday())


def normalize_month_start(window_date: date) -> date:
    return window_date.replace(day=1)


def month_end(window_date: date) -> date:
    next_month = (window_date.replace(day=28) + timedelta(days=4)).replace(day=1)
    return next_month - timedelta(days=1)


def _rolling_window(days: int, now=None):
    now = now or timezone.now()
    days = max(int(days), 1)
    start = now - timedelta(days=days - 1)
    return start, now


def _points_required_for_level(level: int) -> int:
    if level <= 1:
        return 0
    steps_completed = level - 1
    return (steps_completed * LEVEL_BASE_POINTS) + ((steps_completed - 1) * steps_completed // 2 * LEVEL_STEP_POINTS)


def build_level_progress(total_points: int) -> dict[str, int]:
    level = 1
    while total_points >= _points_required_for_level(level + 1):
        level += 1

    level_floor_points = _points_required_for_level(level)
    next_level_points = _points_required_for_level(level + 1)
    return {
        "level": level,
        "level_floor_points": level_floor_points,
        "next_level_points": next_level_points,
        "points_into_level": total_points - level_floor_points,
        "points_to_next_level": next_level_points - total_points,
    }


def build_periodic_review_bounds(review_type: str, anchor_date: date | None = None) -> tuple[date, date]:
    anchor_date = anchor_date or timezone.now().date()
    if review_type == PeriodicReviewType.WEEKLY:
        period_start = normalize_week_start(anchor_date)
        period_end = period_start + timedelta(days=6)
        return period_start, period_end
    if review_type == PeriodicReviewType.MONTHLY:
        period_start = normalize_month_start(anchor_date)
        return period_start, month_end(period_start)
    raise ValueError(f"Unsupported review_type {review_type}")


def review_summary_requirements(review_type: str) -> tuple[str, ...]:
    return REVIEW_SUMMARY_REQUIREMENTS[review_type]


def minimum_transaction_feedback_required(review_type: str) -> int:
    return REVIEW_MINIMUM_TRANSACTION_FEEDBACK[review_type]


def get_or_create_periodic_review(*, user: User, review_type: str, anchor_date: date | None = None) -> PeriodicReview:
    period_start, period_end = build_periodic_review_bounds(review_type, anchor_date)
    review, _ = PeriodicReview.objects.get_or_create(
        user=user,
        review_type=review_type,
        period_start=period_start,
        defaults={"period_end": period_end},
    )
    if review.period_end != period_end:
        review.period_end = period_end
        review.save(update_fields=["period_end", "updated_at"])
    return review


def _transaction_feedback_filter() -> Q:
    return (
        Q(satisfaction_rating__isnull=False)
        | Q(regret_rating__isnull=False)
        | Q(repurchase_likelihood__isnull=False)
        | (Q(reflection_text__isnull=False) & ~Q(reflection_text=""))
    )


def _review_effective_end(review: PeriodicReview, reference_date: date | None = None) -> date:
    today = reference_date or timezone.now().date()
    return min(review.period_end, today)


def reviewed_transactions_count(review: PeriodicReview, reference_date: date | None = None) -> int:
    effective_end = _review_effective_end(review, reference_date)
    return Transaction.objects.filter(
        user=review.user,
        direction="spend",
        occurred_at__date__gte=review.period_start,
        occurred_at__date__lte=effective_end,
    ).filter(_transaction_feedback_filter()).count()


def pending_transaction_feedback_count(review: PeriodicReview, reference_date: date | None = None) -> int:
    effective_end = _review_effective_end(review, reference_date)
    return Transaction.objects.filter(
        user=review.user,
        direction="spend",
        occurred_at__date__gte=review.period_start,
        occurred_at__date__lte=effective_end,
    ).exclude(category__in=LOW_SIGNAL_REVIEW_CATEGORIES).filter(
        satisfaction_rating__isnull=True,
        regret_rating__isnull=True,
        repurchase_likelihood__isnull=True,
    ).filter(Q(reflection_text__isnull=True) | Q(reflection_text="")).count()


def review_transaction_candidates(review: PeriodicReview, reference_date: date | None = None) -> list[Transaction]:
    effective_end = _review_effective_end(review, reference_date)
    days_window = max((effective_end - review.period_start).days + 1, 1)
    limit = REVIEW_TRANSACTION_CANDIDATE_LIMIT[review.review_type]
    raw_candidates = get_feedback_candidates(review.user, days_window=days_window, top_n=max(limit * 3, 10))
    ordered_ids = [candidate["transaction_id"] for candidate in raw_candidates]

    transactions = list(
        Transaction.objects.filter(
            user=review.user,
            id__in=ordered_ids,
            direction="spend",
            occurred_at__date__gte=review.period_start,
            occurred_at__date__lte=effective_end,
        )
        .select_related("merchant", "subscription")
    )
    by_id = {transaction.id: transaction for transaction in transactions}
    ordered_transactions = [by_id[transaction_id] for transaction_id in ordered_ids if transaction_id in by_id]

    if len(ordered_transactions) < limit:
        fallback_transactions = list(
            Transaction.objects.filter(
                user=review.user,
                direction="spend",
                occurred_at__date__gte=review.period_start,
                occurred_at__date__lte=effective_end,
            )
            .exclude(category__in=LOW_SIGNAL_REVIEW_CATEGORIES)
            .exclude(id__in=[transaction.id for transaction in ordered_transactions])
            .filter(
                satisfaction_rating__isnull=True,
                regret_rating__isnull=True,
                repurchase_likelihood__isnull=True,
            )
            .filter(Q(reflection_text__isnull=True) | Q(reflection_text=""))
            .select_related("merchant", "subscription")
            .order_by("-occurred_at")[: max(limit - len(ordered_transactions), 0)]
        )
        ordered_transactions.extend(fallback_transactions)

    return ordered_transactions[:limit]


def upcoming_subscription_renewal_candidates(user: User, *, limit: int = SUBSCRIPTION_RENEWAL_NUDGE_LIMIT) -> list[Subscription]:
    today = timezone.now().date()
    lookahead = today + timedelta(days=14)
    return list(
        Subscription.objects.filter(
            user=user,
            status=SubscriptionStatus.ACTIVE,
            renewal_date__isnull=False,
            renewal_date__gte=today,
            renewal_date__lte=lookahead,
        )
        .select_related("merchant")
        .order_by("renewal_date", "created_at")[:limit]
    )


def low_value_subscription_candidates(user: User, *, limit: int = LOW_VALUE_SUBSCRIPTION_NUDGE_LIMIT) -> list[Subscription]:
    return list(
        Subscription.objects.filter(user=user, status=SubscriptionStatus.ACTIVE)
        .filter(
            Q(subscription_utilization__lt=0.4)
            | Q(subscription_cost_benefit__lt=0.4)
            | Q(feedback_value_score__lt=0.4)
        )
        .select_related("merchant")
        .order_by("renewal_date", "created_at")[:limit]
    )


def periodic_review_period_label(review: PeriodicReview) -> str:
    if review.review_type == PeriodicReviewType.WEEKLY:
        return f"Week of {review.period_start.isoformat()}"
    return review.period_start.strftime("%B %Y")


def can_complete_periodic_review(review: PeriodicReview, *, summary_json: dict | None = None) -> tuple[bool, list[str], int]:
    summary_json = summary_json or review.summary_json or {}
    missing_fields = [
        field for field in review_summary_requirements(review.review_type)
        if not str(summary_json.get(field, "")).strip()
    ]
    reviewed_count = reviewed_transactions_count(review)
    if reviewed_count < minimum_transaction_feedback_required(review.review_type):
        missing_fields.append("reviewed_transactions")
    return len(missing_fields) == 0, missing_fields, reviewed_count


def build_periodic_review_payload(review: PeriodicReview) -> dict:
    reviewed_count = reviewed_transactions_count(review)
    pending_count = pending_transaction_feedback_count(review)
    eligible, _, _ = can_complete_periodic_review(review)
    return {
        "review": review,
        "period_label": periodic_review_period_label(review),
        "summary_requirements": list(review_summary_requirements(review.review_type)),
        "minimum_transactions_required": minimum_transaction_feedback_required(review.review_type),
        "reviewed_transaction_count": reviewed_count,
        "pending_transaction_feedback_count": pending_count,
        "eligible_to_complete": eligible,
        "transaction_candidates": review_transaction_candidates(review),
        "upcoming_subscription_renewals": upcoming_subscription_renewal_candidates(review.user),
        "low_value_subscriptions": low_value_subscription_candidates(review.user),
    }


def build_review_nudges(user: User) -> dict:
    weekly_review = get_or_create_periodic_review(user=user, review_type=PeriodicReviewType.WEEKLY)
    monthly_review = get_or_create_periodic_review(user=user, review_type=PeriodicReviewType.MONTHLY)
    weekly_payload = build_periodic_review_payload(weekly_review)
    monthly_payload = build_periodic_review_payload(monthly_review)
    return {
        "weekly": {
            "review_id": weekly_review.id,
            "status": weekly_review.status,
            "due": weekly_review.status != PeriodicReviewStatus.COMPLETED,
            "period_start": weekly_review.period_start.isoformat(),
            "period_end": weekly_review.period_end.isoformat(),
            "pending_transaction_feedback_count": weekly_payload["pending_transaction_feedback_count"],
            "reviewed_transaction_count": weekly_payload["reviewed_transaction_count"],
            "eligible_to_complete": weekly_payload["eligible_to_complete"],
        },
        "monthly": {
            "review_id": monthly_review.id,
            "status": monthly_review.status,
            "due": monthly_review.status != PeriodicReviewStatus.COMPLETED,
            "period_start": monthly_review.period_start.isoformat(),
            "period_end": monthly_review.period_end.isoformat(),
            "pending_transaction_feedback_count": monthly_payload["pending_transaction_feedback_count"],
            "reviewed_transaction_count": monthly_payload["reviewed_transaction_count"],
            "eligible_to_complete": monthly_payload["eligible_to_complete"],
            "upcoming_subscription_renewals_count": len(monthly_payload["upcoming_subscription_renewals"]),
            "low_value_subscriptions_count": len(monthly_payload["low_value_subscriptions"]),
        },
    }


@transaction.atomic
def complete_periodic_review(
    review: PeriodicReview,
    *,
    summary_json: dict | None,
    notes: str = "",
) -> tuple[PeriodicReview, AwardResult, list[str], int]:
    is_eligible, missing_fields, reviewed_count = can_complete_periodic_review(review, summary_json=summary_json)
    if not is_eligible:
        raise ValueError(",".join(missing_fields))

    review.summary_json = summary_json or {}
    review.notes = notes
    review.status = PeriodicReviewStatus.COMPLETED
    if review.completed_at is None:
        review.completed_at = timezone.now()
    review.save(update_fields=["summary_json", "notes", "status", "completed_at", "updated_at"])

    if review.review_type == PeriodicReviewType.WEEKLY:
        result = award_points_for_weekly_review(review.user, window_start=review.period_start)
    else:
        result = award_points_for_monthly_review(
            review.user,
            month_key=review.period_start.strftime("%Y-%m"),
        )
    return review, result, missing_fields, reviewed_count


def _get_or_create_streak(user: User) -> UserStreak:
    streak, _ = UserStreak.objects.get_or_create(user=user)
    return streak


def _update_streak(user: User, streak_date: date | None = None) -> UserStreak:
    streak = _get_or_create_streak(user)
    qualifying_dates = list(
        PointEvent.objects.filter(
            user=user,
            action__in=STREAK_QUALIFYING_ACTIONS,
            window_date__isnull=False,
        )
        .values_list("window_date", flat=True)
        .distinct()
        .order_by("window_date")
    )

    if not qualifying_dates:
        streak.current_streak_days = 0
        streak.best_streak_days = 0
        streak.last_checkin_date = None
        streak.save(update_fields=["current_streak_days", "best_streak_days", "last_checkin_date", "updated_at"])
        return streak

    best_streak = 1
    current_run = 1
    for idx in range(1, len(qualifying_dates)):
        if qualifying_dates[idx] == qualifying_dates[idx - 1] + timedelta(days=1):
            current_run += 1
        else:
            best_streak = max(best_streak, current_run)
            current_run = 1
    best_streak = max(best_streak, current_run)

    latest_date = qualifying_dates[-1]
    current_streak = 1
    for idx in range(len(qualifying_dates) - 2, -1, -1):
        if qualifying_dates[idx] == latest_date - timedelta(days=current_streak):
            current_streak += 1
            continue
        break

    streak.current_streak_days = current_streak
    streak.best_streak_days = best_streak
    streak.last_checkin_date = latest_date
    streak.save(update_fields=["current_streak_days", "best_streak_days", "last_checkin_date", "updated_at"])
    return streak


def _is_new_active_day(user: User, streak_date: date) -> bool:
    return not PointEvent.objects.filter(
        user=user,
        window_date=streak_date,
        action__in=STREAK_QUALIFYING_ACTIONS,
    ).exists()


def _ensure_badge(user: User, code: str, trigger_event: PointEvent | None, source_object_type="", source_object_id=None) -> UserBadge | None:
    try:
        badge = Badge.objects.get(code=code, is_active=True)
    except Badge.DoesNotExist:
        return None

    user_badge, created = UserBadge.objects.get_or_create(
        user=user,
        badge=badge,
        defaults={
            "source_object_type": source_object_type,
            "source_object_id": source_object_id,
            "trigger_event": trigger_event,
        },
    )
    return user_badge if created else None


def _award_badges_for_event(event: PointEvent) -> list[UserBadge]:
    user = event.user
    awarded: list[UserBadge] = []

    def maybe_award(code: str, *, source_object_type: str = "", source_object_id=None):
        user_badge = _ensure_badge(
            user=user,
            code=code,
            trigger_event=event,
            source_object_type=source_object_type,
            source_object_id=source_object_id,
        )
        if user_badge:
            awarded.append(user_badge)

    if event.action == PointAction.COMPLETE_ONBOARDING:
        maybe_award("first_step", source_object_type=event.source_object_type, source_object_id=event.source_object_id)

    if event.action == PointAction.SET_MONTHLY_TARGET:
        total_targets = MonthlyTarget.objects.filter(user=user).count()
        if total_targets >= 1:
            maybe_award("goal_setter")

    if event.action == PointAction.LOG_PURCHASE:
        purchase_count = PointEvent.objects.filter(user=user, action=PointAction.LOG_PURCHASE).count()
        category_count = (
            PointEvent.objects
            .filter(user=user, action=PointAction.LOG_PURCHASE)
            .values_list("metadata_json__category", flat=True)
            .distinct()
            .count()
        )
        if purchase_count >= 1:
            maybe_award("first_log", source_object_type=event.source_object_type, source_object_id=event.source_object_id)
        if purchase_count >= 5:
            maybe_award("money_tracker_1")
        if purchase_count >= 25:
            maybe_award("money_tracker_2")
        if purchase_count >= 100:
            maybe_award("money_tracker_3")
        if category_count >= 5:
            maybe_award("category_explorer")

    if event.action == PointAction.LOG_PURCHASE_NEW_DAY:
        active_days = (
            PointEvent.objects
            .filter(user=user, action=PointAction.LOG_PURCHASE_NEW_DAY)
            .values("window_date")
            .distinct()
            .count()
        )
        if active_days >= 7:
            maybe_award("consistent_logger")

    if event.action in {PointAction.REFLECT_SAME_DAY, PointAction.REFLECT_RISKY_PURCHASE}:
        same_day_count = PointEvent.objects.filter(user=user, action=PointAction.REFLECT_SAME_DAY).count()
        reflection_count = TransactionReflection.objects.filter(user=user).count()
        if reflection_count >= 1:
            maybe_award("honest_check_in", source_object_type=event.source_object_type, source_object_id=event.source_object_id)
        if reflection_count >= 5:
            maybe_award("reflection_rookie")
        if reflection_count >= 20:
            maybe_award("reflection_habit")
        if same_day_count >= 1:
            maybe_award("same_day_thinker")
        if same_day_count >= 7:
            maybe_award("no_regret_zone")
        if same_day_count >= 15:
            maybe_award("mindful_buyer")
        risky_count = PointEvent.objects.filter(user=user, action=PointAction.REFLECT_RISKY_PURCHASE).count()
        if risky_count >= 3:
            maybe_award("pause_and_think")

    if event.action in {PointAction.USE_ADVISOR, PointAction.FIRST_ADVISOR_USE}:
        advisor_count = PointEvent.objects.filter(
            user=user,
            action__in=[PointAction.USE_ADVISOR, PointAction.FIRST_ADVISOR_USE],
        ).count()
        if advisor_count >= 1:
            maybe_award("advisor_curious", source_object_type=event.source_object_type, source_object_id=event.source_object_id)

    if event.action == PointAction.RUN_ITEM_VALUATION:
        count = PointEvent.objects.filter(user=user, action=PointAction.RUN_ITEM_VALUATION).count()
        if count >= 5:
            maybe_award("smart_shopper")
        if count >= 20:
            maybe_award("value_checker")
        if count >= 10:
            maybe_award("price_detective")

    if event.action == PointAction.ADD_SUBSCRIPTION:
        count = PointEvent.objects.filter(user=user, action=PointAction.ADD_SUBSCRIPTION).count()
        if count >= 1:
            maybe_award("subscription_starter", source_object_type=event.source_object_type, source_object_id=event.source_object_id)
        if count >= 5:
            maybe_award("subscription_mapper")

    if event.action in {PointAction.CANCEL_SUBSCRIPTION, PointAction.PAUSE_SUBSCRIPTION}:
        count = PointEvent.objects.filter(
            user=user,
            action__in=[PointAction.CANCEL_SUBSCRIPTION, PointAction.PAUSE_SUBSCRIPTION],
        ).count()
        if count >= 1:
            maybe_award("cleanup_crew", source_object_type=event.source_object_type, source_object_id=event.source_object_id)

    if event.action == PointAction.REVIEW_UPCOMING_RENEWAL:
        maybe_award("renewal_ready")

    if event.action == PointAction.COMPLETE_SUBSCRIPTION_AUDIT:
        maybe_award("subscription_auditor")

    if event.action == PointAction.FOLLOW_WAIT_RECOMMENDATION:
        count = PointEvent.objects.filter(user=user, action=PointAction.FOLLOW_WAIT_RECOMMENDATION).count()
        if count >= 3:
            maybe_award("wait_warrior")

    if event.action == PointAction.JOIN_GROUP:
        maybe_award("group_ready", source_object_type="group", source_object_id=event.group_id)
        maybe_award("friendly_rival", source_object_type="group", source_object_id=event.group_id)

    if event.action == PointAction.CREATE_GROUP:
        maybe_award("founder", source_object_type="group", source_object_id=event.group_id)

    if event.action == PointAction.LEADERBOARD_TOP_THREE:
        maybe_award("podium_finish", source_object_type="group", source_object_id=event.group_id)

    if event.action == PointAction.LEADERBOARD_WINNER:
        maybe_award("weekly_winner", source_object_type="group", source_object_id=event.group_id)

    if event.action == PointAction.COMPLETE_MONTHLY_TARGET:
        total_completed_targets = PointEvent.objects.filter(user=user, action=PointAction.COMPLETE_MONTHLY_TARGET).count()
        maybe_award("monthly_momentum")
        if total_completed_targets >= 3:
            maybe_award("target_taker")
        month_key = event.metadata_json.get("month")
        if month_key:
            month_start = date.fromisoformat(f"{month_key}-01")
            month_targets = MonthlyTarget.objects.filter(user=user, month_start=month_start)
            if month_targets.exists() and not month_targets.exclude(status=MonthlyTargetStatus.COMPLETED).exists():
                maybe_award("month_master")
        distinct_months = (
            PointEvent.objects
            .filter(user=user, action=PointAction.COMPLETE_MONTHLY_TARGET)
            .values("window_date")
            .distinct()
            .count()
        )
        if distinct_months >= 2:
            maybe_award("consistency_champ")

    streak = getattr(user, "streak", None)
    if streak:
        if streak.best_streak_days >= 3:
            maybe_award("getting_started")
        if streak.best_streak_days >= 7:
            maybe_award("locked_in")
        if streak.best_streak_days >= 14:
            maybe_award("habit_builder")
        if streak.best_streak_days >= 30:
            maybe_award("unstoppable")
        if streak.best_streak_days >= 60:
            maybe_award("iron_discipline")

    return awarded


@transaction.atomic
def award_points(
    *,
    user: User,
    action: str,
    points: int | None = None,
    group: Group | None = None,
    source_object_type: str = "",
    source_object_id=None,
    event_key: str | None = None,
    metadata_json: dict | None = None,
    window_date: date | None = None,
    streak_date: date | None = None,
) -> AwardResult:
    event_key = _normalize_event_key(event_key)
    defaults = {
        "user": user,
        "group": group,
        "action": action,
        "points": ACTION_POINTS[action] if points is None else points,
        "source_object_type": source_object_type,
        "source_object_id": source_object_id,
        "metadata_json": metadata_json or {},
        "window_date": window_date or timezone.now().date(),
    }

    if event_key:
        event, created = PointEvent.objects.get_or_create(event_key=event_key, defaults=defaults)
    else:
        event = PointEvent.objects.create(**defaults)
        created = True

    if not created:
        return AwardResult(event=event, created=False, badges_awarded=[])

    if action in STREAK_QUALIFYING_ACTIONS:
        streak = _update_streak(user, streak_date=streak_date or event.window_date)
    else:
        streak = _get_or_create_streak(user)
    streak.total_points_earned = max(streak.total_points_earned + event.points, 0)
    streak.save(update_fields=["total_points_earned", "updated_at"])
    badges = _award_badges_for_event(event)
    return AwardResult(event=event, created=True, badges_awarded=badges)


def award_points_for_transaction(transaction, *, group: Group | None = None) -> AwardResult:
    streak_date = transaction.occurred_at.date()
    was_new_day = _is_new_active_day(transaction.user, streak_date)
    result = award_points(
        user=transaction.user,
        action=PointAction.LOG_PURCHASE,
        group=group,
        source_object_type="transaction",
        source_object_id=transaction.id,
        event_key=f"log_purchase:txn:{transaction.id}",
        metadata_json={"transaction_id": transaction.id, "category": transaction.category},
        window_date=streak_date,
        streak_date=streak_date,
    )
    if was_new_day:
        award_points(
            user=transaction.user,
            action=PointAction.LOG_PURCHASE_NEW_DAY,
            group=group,
            source_object_type="transaction",
            source_object_id=transaction.id,
            event_key=f"log_purchase_new_day:user:{transaction.user_id}:{streak_date.isoformat()}",
            metadata_json={"transaction_id": transaction.id},
            window_date=streak_date,
            streak_date=streak_date,
        )
        streak = _get_or_create_streak(transaction.user)
        if streak.current_streak_days == 7:
            award_points(
                user=transaction.user,
                action=PointAction.STREAK_7_BONUS,
                source_object_type="streak",
                source_object_id=transaction.user_id,
                event_key=f"streak_7_bonus:user:{transaction.user_id}:{streak_date.isoformat()}",
                window_date=streak_date,
                streak_date=streak_date,
            )
        if streak.current_streak_days == 14:
            award_points(
                user=transaction.user,
                action=PointAction.STREAK_14_BONUS,
                source_object_type="streak",
                source_object_id=transaction.user_id,
                event_key=f"streak_14_bonus:user:{transaction.user_id}:{streak_date.isoformat()}",
                window_date=streak_date,
                streak_date=streak_date,
            )
        if streak.current_streak_days == 30:
            award_points(
                user=transaction.user,
                action=PointAction.STREAK_30_BONUS,
                source_object_type="streak",
                source_object_id=transaction.user_id,
                event_key=f"streak_30_bonus:user:{transaction.user_id}:{streak_date.isoformat()}",
                window_date=streak_date,
                streak_date=streak_date,
            )
    return result


def award_points_for_same_day_reflection(reflection, *, group: Group | None = None) -> AwardResult | None:
    if not reflection.reflected_same_day:
        return None
    result = award_points(
        user=reflection.user,
        action=PointAction.REFLECT_SAME_DAY,
        group=group,
        source_object_type="transaction_reflection",
        source_object_id=reflection.id,
        event_key=f"reflect_same_day:txn:{reflection.transaction_id}",
        metadata_json={"transaction_id": reflection.transaction_id, "reflection_id": reflection.id},
        window_date=reflection.reflected_at.date(),
        streak_date=reflection.reflected_at.date(),
    )
    if reflection.transaction and getattr(reflection.transaction, "impulse_score", None):
        if reflection.transaction.impulse_score >= 0.7:
            award_points(
                user=reflection.user,
                action=PointAction.REFLECT_RISKY_PURCHASE,
                group=group,
                source_object_type="transaction_reflection",
                source_object_id=reflection.id,
                event_key=f"reflect_risky_purchase:txn:{reflection.transaction_id}",
                metadata_json={"transaction_id": reflection.transaction_id, "reflection_id": reflection.id},
                window_date=reflection.reflected_at.date(),
                streak_date=reflection.reflected_at.date(),
            )
    return result


def award_points_for_subscription_added(subscription, *, group: Group | None = None) -> AwardResult:
    return award_points(
        user=subscription.user,
        action=PointAction.ADD_SUBSCRIPTION,
        group=group,
        source_object_type="subscription",
        source_object_id=subscription.id,
        event_key=f"add_subscription:sub:{subscription.id}",
        metadata_json={"subscription_id": subscription.id, "status": subscription.status},
        window_date=timezone.now().date(),
    )


def award_points_for_subscription_cancelled(subscription, *, group: Group | None = None) -> AwardResult:
    return award_points(
        user=subscription.user,
        action=PointAction.CANCEL_SUBSCRIPTION,
        group=group,
        source_object_type="subscription",
        source_object_id=subscription.id,
        event_key=f"cancel_subscription:sub:{subscription.id}",
        metadata_json={"subscription_id": subscription.id, "status": subscription.status},
        window_date=timezone.now().date(),
    )


def award_points_for_subscription_paused(subscription, *, group: Group | None = None) -> AwardResult:
    return award_points(
        user=subscription.user,
        action=PointAction.PAUSE_SUBSCRIPTION,
        group=group,
        source_object_type="subscription",
        source_object_id=subscription.id,
        event_key=f"pause_subscription:sub:{subscription.id}",
        metadata_json={"subscription_id": subscription.id, "status": subscription.status},
        window_date=timezone.now().date(),
    )


def award_points_for_item_valuation(item_valuation, *, group: Group | None = None) -> AwardResult:
    existing_advisor_use = PointEvent.objects.filter(
        user=item_valuation.user,
        action__in=[PointAction.USE_ADVISOR, PointAction.FIRST_ADVISOR_USE],
    ).exists()
    if not existing_advisor_use:
        award_points(
            user=item_valuation.user,
            action=PointAction.FIRST_ADVISOR_USE,
            group=group,
            source_object_type="item_valuation",
            source_object_id=item_valuation.id,
            event_key=f"first_advisor_use:user:{item_valuation.user_id}",
            metadata_json={"item_valuation_id": item_valuation.id},
            window_date=timezone.now().date(),
        )
    award_points(
        user=item_valuation.user,
        action=PointAction.USE_ADVISOR,
        group=group,
        source_object_type="item_valuation",
        source_object_id=item_valuation.id,
        event_key=f"use_advisor:item_valuation:{item_valuation.id}",
        metadata_json={"item_valuation_id": item_valuation.id, "item_name": item_valuation.item_name},
        window_date=timezone.now().date(),
    )
    return award_points(
        user=item_valuation.user,
        action=PointAction.RUN_ITEM_VALUATION,
        group=group,
        source_object_type="item_valuation",
        source_object_id=item_valuation.id,
        event_key=f"run_item_valuation:item_valuation:{item_valuation.id}",
        metadata_json={"item_valuation_id": item_valuation.id, "item_name": item_valuation.item_name},
        window_date=timezone.now().date(),
    )


def award_points_for_subscription_valuation(subscription_valuation, *, group: Group | None = None) -> AwardResult:
    return award_points(
        user=subscription_valuation.user,
        action=PointAction.RUN_SUBSCRIPTION_VALUATION,
        group=group,
        source_object_type="subscription_valuation",
        source_object_id=subscription_valuation.id,
        event_key=f"run_subscription_valuation:subval:{subscription_valuation.id}",
        metadata_json={"subscription_valuation_id": subscription_valuation.id},
        window_date=subscription_valuation.period_end,
        streak_date=subscription_valuation.period_end,
    )


def award_points_for_group_join(user: User, group: Group) -> AwardResult:
    return award_points(
        user=user,
        group=group,
        action=PointAction.JOIN_GROUP,
        source_object_type="group",
        source_object_id=group.id,
        event_key=f"join_group:group:{group.id}:user:{user.id}",
        metadata_json={"group_id": group.id},
    )


def award_points_for_group_create(user: User, group: Group) -> AwardResult:
    return award_points(
        user=user,
        group=group,
        action=PointAction.CREATE_GROUP,
        source_object_type="group",
        source_object_id=group.id,
        event_key=f"create_group:group:{group.id}:owner:{user.id}",
        metadata_json={"group_id": group.id},
    )


def award_points_for_onboarding(user: User) -> AwardResult:
    return award_points(
        user=user,
        action=PointAction.COMPLETE_ONBOARDING,
        source_object_type="user",
        source_object_id=user.id,
        event_key=f"complete_onboarding:user:{user.id}",
        metadata_json={"user_id": user.id},
    )


def award_points_for_weekly_review(user: User, *, window_start: date | None = None) -> AwardResult:
    window_start = normalize_week_start(window_start or timezone.now().date())
    return award_points(
        user=user,
        action=PointAction.WEEKLY_REVIEW,
        source_object_type="user",
        source_object_id=user.id,
        event_key=build_weekly_review_event_key(user, window_start),
        window_date=window_start,
        metadata_json={"window_start": window_start.isoformat()},
    )


def award_points_for_monthly_review(user: User, *, month_key: str) -> AwardResult:
    month_date = date.fromisoformat(f"{month_key}-01")
    return award_points(
        user=user,
        action=PointAction.COMPLETE_MONTHLY_REVIEW,
        source_object_type="user",
        source_object_id=user.id,
        event_key=build_monthly_review_event_key(user, month_key),
        window_date=month_date,
        metadata_json={"month": month_key},
    )


def award_points_for_monthly_target_set(user: User, *, target_type: str, month_key: str) -> AwardResult:
    month_date = date.fromisoformat(f"{month_key}-01")
    return award_points(
        user=user,
        action=PointAction.SET_MONTHLY_TARGET,
        source_object_type="monthly_target",
        source_object_id=user.id,
        event_key=f"set_first_monthly_target:user:{user.id}",
        window_date=month_date,
        metadata_json={"target_type": target_type, "month": month_key},
    )


def award_points_for_monthly_target_completed(user: User, *, target_type: str, month_key: str) -> AwardResult:
    month_date = date.fromisoformat(f"{month_key}-01")
    return award_points(
        user=user,
        action=PointAction.COMPLETE_MONTHLY_TARGET,
        source_object_type="monthly_target",
        source_object_id=user.id,
        event_key=build_complete_monthly_target_event_key(user, target_type, month_key),
        window_date=month_date,
        metadata_json={"target_type": target_type, "month": month_key},
    )


def award_points_for_monthly_target(target: MonthlyTarget) -> AwardResult:
    month_key = target.month_start.strftime("%Y-%m")
    return award_points_for_monthly_target_set(target.user, target_type=target.target_type, month_key=month_key)


def complete_monthly_target(target: MonthlyTarget) -> AwardResult:
    target.status = MonthlyTargetStatus.COMPLETED
    target.current_value = max(target.current_value, target.target_value)
    target.completed_at = timezone.now()
    target.save(update_fields=["status", "current_value", "completed_at", "updated_at"])
    month_key = target.month_start.strftime("%Y-%m")
    return award_points_for_monthly_target_completed(target.user, target_type=target.target_type, month_key=month_key)


def leaderboard_for_group(*, group: Group, days: int = 7):
    start, end = _rolling_window(days)
    start_date = start.date()
    end_date = end.date()
    qs = PointEvent.objects.filter(group=group).filter(
        Q(window_date__gte=start_date, window_date__lte=end_date)
        | Q(window_date__isnull=True, created_at__gte=start, created_at__lte=end)
    )

    totals = (
        qs.values("user__id", "user__username")
        .annotate(points_total=Sum("points"))
        .order_by("-points_total", "user__username")
    )
    active_days = (
        qs.values("user__id", "window_date")
        .exclude(window_date__isnull=True)
        .distinct()
        .values("user__id")
        .annotate(active_days_count=Count("window_date"))
    )
    reflections = (
        qs.filter(action=PointAction.REFLECT_SAME_DAY)
        .values("user__id")
        .annotate(reflections_count=Count("id"))
    )

    active_days_map = {row["user__id"]: row["active_days_count"] for row in active_days}
    reflections_map = {row["user__id"]: row["reflections_count"] for row in reflections}
    totals_map = {row["user__id"]: row["points_total"] or 0 for row in totals}
    memberships = list(
        GroupMember.objects.filter(group=group)
        .select_related("user")
        .order_by("user__username")
    )
    streaks_map = {
        streak.user_id: streak
        for streak in UserStreak.objects.filter(user_id__in=[membership.user_id for membership in memberships])
    }

    rows = []
    for membership in memberships:
        user = membership.user
        user_id = user.id
        total_points = totals_map.get(user_id, 0)
        streak = streaks_map.get(user_id)
        level_progress = build_level_progress(streak.total_points_earned if streak else 0)
        rows.append(
            {
                "user_id": user_id,
                "username": user.username,
                "role": membership.role,
                "points_total": total_points,
                "active_days_count": active_days_map.get(user_id, 0),
                "reflections_count": reflections_map.get(user_id, 0),
                "current_streak_days": streak.current_streak_days if streak else 0,
                "best_streak_days": streak.best_streak_days if streak else 0,
                "lifetime_points_total": streak.total_points_earned if streak else 0,
                "level": level_progress["level"],
            }
        )

    rows.sort(key=lambda row: (-row["points_total"], row["username"]))
    for rank, row in enumerate(rows, start=1):
        row["rank"] = rank

    return {
        "start": start,
        "end": end,
        "days": days,
        "member_count": len(rows),
        "rows": rows,
    }
