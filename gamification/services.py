from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Count, Sum
from django.utils import timezone

from transactions.models import TransactionReflection

from .badges import ACTION_POINTS, BADGE_CATALOG
from .models import Badge, Group, GroupMember, MonthlyTarget, MonthlyTargetStatus, PointAction, PointEvent, UserBadge, UserStreak

User = get_user_model()

STREAK_QUALIFYING_ACTIONS = {
    PointAction.LOG_PURCHASE,
    PointAction.REFLECT_SAME_DAY,
    PointAction.USE_ADVISOR,
}

LEVEL_BASE_POINTS = 50
LEVEL_STEP_POINTS = 25


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


def _rolling_window(days: int, now=None):
    now = now or timezone.now()
    start = now - timedelta(days=days)
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


def _get_or_create_streak(user: User) -> UserStreak:
    streak, _ = UserStreak.objects.get_or_create(user=user)
    return streak


def _update_streak(user: User, streak_date: date | None = None) -> UserStreak:
    streak_date = streak_date or timezone.now().date()
    streak = _get_or_create_streak(user)

    if streak.last_checkin_date == streak_date:
        return streak

    if streak.last_checkin_date == streak_date - timedelta(days=1):
        streak.current_streak_days += 1
    else:
        streak.current_streak_days = 1

    streak.last_checkin_date = streak_date
    if streak.current_streak_days > streak.best_streak_days:
        streak.best_streak_days = streak.current_streak_days
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
    sync_badge_catalog()

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
    qs = PointEvent.objects.filter(group=group, created_at__gte=start, created_at__lte=end)

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
