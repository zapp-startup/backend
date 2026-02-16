from datetime import timedelta
from django.db.models import Sum, Count
from django.utils import timezone

from rest_framework import status
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.viewsets import ModelViewSet, ReadOnlyModelViewSet

from .models import Group, GroupMember, PointEvent, UserStreak, PointAction
from .serializers import GroupSerializer, GroupMemberSerializer, PointEventSerializer, UserStreakSerializer


def _week_window(now):
    # Simple weekly window: last 7 days rolling.
    # (Later: use a Season table with fixed start/end)
    start = now - timedelta(days=7)
    return start, now


def _get_or_create_streak(user):
    streak, _ = UserStreak.objects.get_or_create(user=user)
    return streak


def _update_streak_for_today(user):
    today = timezone.now().date()
    streak = _get_or_create_streak(user)

    if streak.last_checkin_date == today:
        return streak  # already checked in today

    if streak.last_checkin_date == today - timedelta(days=1):
        streak.current_streak_days += 1
    else:
        streak.current_streak_days = 1

    streak.last_checkin_date = today
    if streak.current_streak_days > streak.best_streak_days:
        streak.best_streak_days = streak.current_streak_days
    streak.save()
    return streak


class GroupViewSet(ModelViewSet):
    serializer_class = GroupSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        # groups the user is a member of
        return Group.objects.filter(memberships__user=self.request.user).distinct()

    def perform_create(self, serializer):
        # generate invite code in a simple way for MVP
        # You can replace this with something stronger later.
        import secrets
        invite_code = secrets.token_hex(4)  # 8 chars
        group = serializer.save(created_by=self.request.user, invite_code=invite_code)

        # auto-add creator as admin member
        GroupMember.objects.create(group=group, user=self.request.user, role="admin")

    @action(detail=False, methods=["post"])
    def join(self, request):
        code = request.data.get("invite_code", "").strip()
        if not code:
            return Response({"detail": "invite_code required"}, status=400)

        try:
            group = Group.objects.get(invite_code=code)
        except Group.DoesNotExist:
            return Response({"detail": "Invalid invite code"}, status=404)

        GroupMember.objects.get_or_create(group=group, user=request.user)
        return Response({"detail": "Joined group"}, status=200)


class PointEventViewSet(ModelViewSet):
    serializer_class = PointEventSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return PointEvent.objects.filter(user=self.request.user).select_related("group")

    def perform_create(self, serializer):
        # MVP: allow client-triggered points with validation later.
        # In production you’d usually award points server-side only.
        serializer.save(user=self.request.user)
        _update_streak_for_today(self.request.user)

    @action(detail=False, methods=["get"])
    def my_streak(self, request):
        streak = _get_or_create_streak(request.user)
        return Response(UserStreakSerializer(streak).data)

    @action(detail=False, methods=["get"])
    def leaderboard(self, request):
        """
        GET /gamification/points/leaderboard/?group_id=123
        Returns weekly leaderboard: points total, active days, reflections count.
        """
        group_id = request.query_params.get("group_id")
        if not group_id:
            return Response({"detail": "group_id required"}, status=400)

        # verify user is in group
        if not GroupMember.objects.filter(group_id=group_id, user=request.user).exists():
            return Response({"detail": "Not a member of this group"}, status=403)

        now = timezone.now()
        start, end = _week_window(now)

        qs = PointEvent.objects.filter(group_id=group_id, created_at__gte=start, created_at__lte=end)

        # points total per user
        totals = (
            qs.values("user__id", "user__username")
            .annotate(points_total=Sum("points"))
            .order_by("-points_total")
        )

        # reflections count per user
        reflections = (
            qs.filter(action=PointAction.REFLECT_SAME_DAY)
            .values("user__id")
            .annotate(reflections_count=Count("id"))
        )
        reflections_map = {r["user__id"]: r["reflections_count"] for r in reflections}

        # active days per user (approx: count distinct dates)
        # DB portable approach: store a date field later if needed.
        # For MVP: approximate by counting events and treating >=1/day as activity
        # (or migrate to a proper distinct-date query later).
        # We'll just return points + reflections for now.
        out = []
        for row in totals:
            uid = row["user__id"]
            out.append({
                "user_id": uid,
                "username": row["user__username"],
                "points_total": row["points_total"] or 0,
                "reflections_count": reflections_map.get(uid, 0),
            })

        return Response({"start": start, "end": end, "rows": out})
