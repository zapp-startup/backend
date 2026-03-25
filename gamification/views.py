import secrets
from datetime import date
from datetime import timedelta

from django.db import transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied
from rest_framework.pagination import PageNumberPagination
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.viewsets import ModelViewSet, ReadOnlyModelViewSet

from .models import (
    Badge,
    Group,
    GroupInvite,
    GroupInviteStatus,
    GroupMember,
    GroupRole,
    MonthlyTarget,
    MonthlyTargetStatus,
    PointEvent,
    UserBadge,
    UserStreak,
)
from .serializers import (
    BadgeSerializer,
    GroupInviteSerializer,
    GroupMemberSerializer,
    GroupSerializer,
    MonthlyTargetSerializer,
    PointEventSerializer,
    UserBadgeSerializer,
    UserStreakSerializer,
)
from .services import (
    award_points_for_group_create,
    award_points_for_group_join,
    award_points_for_monthly_target,
    award_points_for_monthly_review,
    award_points_for_weekly_review,
    complete_monthly_target,
    leaderboard_for_group,
)


def _generate_invite_code() -> str:
    return secrets.token_urlsafe(9).replace("-", "").replace("_", "")[:12]


def _is_group_admin(user, group: Group) -> bool:
    return GroupMember.objects.filter(user=user, group=group, role=GroupRole.ADMIN).exists()


@transaction.atomic
def _leave_group(user, group: Group):
    membership = get_object_or_404(GroupMember, user=user, group=group)
    membership.delete()

    remaining = GroupMember.objects.filter(group=group).select_related("user").order_by("joined_at")
    if not remaining.exists():
        group.delete()
        return {"detail": "Left group. Group was deleted because it had no members left.", "group_deleted": True}

    if group.created_by_id == user.id:
        next_admin = remaining.filter(role=GroupRole.ADMIN).first() or remaining.first()
        if next_admin.role != GroupRole.ADMIN:
            next_admin.role = GroupRole.ADMIN
            next_admin.save(update_fields=["role"])
        group.created_by = next_admin.user
        group.save(update_fields=["created_by"])

    return {"detail": "Left group.", "group_deleted": False}


def _join_group_from_invite(request_user, group: Group, invite: GroupInvite | None = None):
    membership, _ = GroupMember.objects.get_or_create(
        group=group,
        user=request_user,
        defaults={"role": GroupRole.MEMBER},
    )
    if membership.role != GroupRole.MEMBER and membership.user_id == request_user.id and membership.role != GroupRole.ADMIN:
        membership.role = GroupRole.MEMBER
        membership.save(update_fields=["role"])

    if invite and invite.status == GroupInviteStatus.PENDING:
        invite.status = GroupInviteStatus.ACCEPTED
        invite.accepted_by = request_user
        invite.accepted_at = timezone.now()
        invite.save(update_fields=["status", "accepted_by", "accepted_at"])

    award_points_for_group_join(request_user, group)
    return membership


def _is_last_admin(group: Group, membership: GroupMember) -> bool:
    return membership.role == GroupRole.ADMIN and GroupMember.objects.filter(group=group, role=GroupRole.ADMIN).count() == 1


class CircleLeaderboardPagination(PageNumberPagination):
    page_size = 10
    page_size_query_param = "page_size"
    max_page_size = 50


class GroupViewSet(ModelViewSet):
    serializer_class = GroupSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return Group.objects.filter(memberships__user=self.request.user).distinct()

    def perform_create(self, serializer):
        invite_code = _generate_invite_code()
        group = serializer.save(created_by=self.request.user, invite_code=invite_code)
        GroupMember.objects.get_or_create(group=group, user=self.request.user, defaults={"role": GroupRole.ADMIN})
        award_points_for_group_create(self.request.user, group)

    def perform_update(self, serializer):
        group = self.get_object()
        if not _is_group_admin(self.request.user, group):
            raise PermissionDenied("Only group admins can update the group.")
        serializer.save()

    def destroy(self, request, *args, **kwargs):
        group = self.get_object()
        if not _is_group_admin(request.user, group):
            return Response({"detail": "Only group admins can delete the group."}, status=403)
        return super().destroy(request, *args, **kwargs)

    @action(detail=False, methods=["post"])
    def join(self, request):
        code = request.data.get("invite_code", "").strip()
        if not code:
            return Response({"detail": "invite_code required"}, status=400)

        now = timezone.now()
        invite = (
            GroupInvite.objects
            .filter(invite_code=code, status=GroupInviteStatus.PENDING)
            .select_related("group")
            .first()
        )
        if invite:
            if invite.expires_at and invite.expires_at <= now:
                return Response({"detail": "Invite code expired"}, status=400)
            if invite.invited_user_id and invite.invited_user_id != request.user.id:
                return Response({"detail": "This invite is not for you"}, status=403)
            _join_group_from_invite(request.user, invite.group, invite=invite)
            return Response({"detail": "Joined group", "group_id": invite.group_id}, status=200)

        try:
            group = Group.objects.get(invite_code=code)
        except Group.DoesNotExist:
            return Response({"detail": "Invalid invite code"}, status=404)

        _join_group_from_invite(request.user, group)
        return Response({"detail": "Joined group", "group_id": group.id}, status=200)

    @action(detail=True, methods=["post"])
    def leave(self, request, pk=None):
        group = self.get_object()
        result = _leave_group(request.user, group)
        return Response(result, status=200)

    @action(detail=True, methods=["get"])
    def members(self, request, pk=None):
        group = self.get_object()
        memberships = GroupMember.objects.filter(group=group).select_related("user").order_by("joined_at")
        return Response(GroupMemberSerializer(memberships, many=True).data)

    @action(detail=True, methods=["post"])
    def update_member_role(self, request, pk=None):
        group = self.get_object()
        if not _is_group_admin(request.user, group):
            raise PermissionDenied("Only group admins can update member roles.")

        membership_id = request.data.get("membership_id")
        role = request.data.get("role")
        if role not in {GroupRole.ADMIN, GroupRole.MEMBER}:
            return Response({"detail": "role must be admin or member"}, status=400)

        membership = get_object_or_404(GroupMember, id=membership_id, group=group)
        if group.created_by_id == membership.user_id and role != GroupRole.ADMIN:
            return Response({"detail": "The group owner must remain an admin."}, status=400)
        if membership.user_id == request.user.id and _is_last_admin(group, membership) and role != GroupRole.ADMIN:
            return Response({"detail": "The last admin cannot demote themselves."}, status=400)

        membership.role = role
        membership.save(update_fields=["role"])
        return Response(GroupMemberSerializer(membership).data)

    @action(detail=True, methods=["post"])
    def remove_member(self, request, pk=None):
        group = self.get_object()
        if not _is_group_admin(request.user, group):
            raise PermissionDenied("Only group admins can remove members.")

        membership_id = request.data.get("membership_id")
        membership = get_object_or_404(GroupMember, id=membership_id, group=group)
        if membership.user_id == request.user.id:
            return Response({"detail": "Use leave to remove yourself."}, status=400)
        if group.created_by_id == membership.user_id:
            return Response({"detail": "Use owner transfer or leave flow for the group owner."}, status=400)
        if _is_last_admin(group, membership):
            return Response({"detail": "Cannot remove the last admin."}, status=400)

        membership.delete()
        return Response(status=204)


class GroupInviteViewSet(ModelViewSet):
    serializer_class = GroupInviteSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        admin_q = Q(group__memberships__user=self.request.user, group__memberships__role=GroupRole.ADMIN)
        recipient_q = Q(invited_user=self.request.user)
        return (
            GroupInvite.objects
            .filter(admin_q | recipient_q)
            .select_related("group", "invited_by", "invited_user", "accepted_by")
            .distinct()
            .order_by("-created_at")
        )

    def perform_create(self, serializer):
        group = serializer.validated_data["group"]
        if not _is_group_admin(self.request.user, group):
            raise PermissionDenied("Only group admins can create invites.")

        expires_at = serializer.validated_data.get("expires_at")
        if expires_at is None:
            expires_at = timezone.now() + timedelta(days=7)

        serializer.save(
            invited_by=self.request.user,
            invite_code=_generate_invite_code(),
            expires_at=expires_at,
        )

    def destroy(self, request, *args, **kwargs):
        invite = self.get_object()
        if not _is_group_admin(request.user, invite.group) and invite.invited_by_id != request.user.id:
            return Response({"detail": "Only group admins can revoke invites."}, status=403)
        if invite.status == GroupInviteStatus.ACCEPTED:
            return Response({"detail": "Accepted invites cannot be revoked."}, status=400)
        invite.status = GroupInviteStatus.REVOKED
        invite.revoked_at = timezone.now()
        invite.save(update_fields=["status", "revoked_at"])
        return Response(status=204)

    def update(self, request, *args, **kwargs):
        return Response({"detail": "Invites cannot be updated."}, status=405)

    def partial_update(self, request, *args, **kwargs):
        return Response({"detail": "Invites cannot be updated."}, status=405)

    @action(detail=True, methods=["post"])
    def accept(self, request, pk=None):
        invite = self.get_object()
        if invite.status != GroupInviteStatus.PENDING:
            return Response({"detail": "Invite is not pending."}, status=400)
        if invite.expires_at and invite.expires_at <= timezone.now():
            return Response({"detail": "Invite code expired"}, status=400)
        if invite.invited_user_id and invite.invited_user_id != request.user.id:
            return Response({"detail": "This invite is not for you"}, status=403)

        _join_group_from_invite(request.user, invite.group, invite=invite)
        return Response({"detail": "Invite accepted", "group_id": invite.group_id}, status=200)

    @action(detail=True, methods=["post"])
    def decline(self, request, pk=None):
        invite = self.get_object()
        if invite.status != GroupInviteStatus.PENDING:
            return Response({"detail": "Invite is not pending."}, status=400)
        if invite.invited_user_id and invite.invited_user_id != request.user.id:
            return Response({"detail": "This invite is not for you"}, status=403)
        invite.status = GroupInviteStatus.DECLINED
        invite.save(update_fields=["status"])
        return Response({"detail": "Invite declined."}, status=200)


class MonthlyTargetViewSet(ModelViewSet):
    serializer_class = MonthlyTargetSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return MonthlyTarget.objects.filter(user=self.request.user).order_by("-month_start", "-created_at")

    def perform_create(self, serializer):
        month_start = serializer.validated_data["month_start"].replace(day=1)
        target = serializer.save(user=self.request.user, month_start=month_start)
        award_points_for_monthly_target(target)

    def perform_update(self, serializer):
        target = serializer.save()
        if target.current_value >= target.target_value and target.status != MonthlyTargetStatus.COMPLETED:
            complete_monthly_target(target)

    @action(detail=True, methods=["post"])
    def progress(self, request, pk=None):
        target = self.get_object()
        try:
            amount = int(request.data.get("amount", 0))
        except (TypeError, ValueError):
            return Response({"detail": "amount must be an integer"}, status=400)
        if amount <= 0:
            return Response({"detail": "amount must be positive"}, status=400)
        target.current_value += amount
        if target.current_value >= target.target_value and target.status != MonthlyTargetStatus.COMPLETED:
            complete_monthly_target(target)
        else:
            target.save(update_fields=["current_value", "updated_at"])
        target.refresh_from_db()
        return Response(MonthlyTargetSerializer(target).data)


class PointEventViewSet(ReadOnlyModelViewSet):
    serializer_class = PointEventSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return PointEvent.objects.filter(user=self.request.user).select_related("group")

    @action(detail=False, methods=["get"])
    def my_streak(self, request):
        streak, _ = UserStreak.objects.get_or_create(user=request.user)
        return Response(UserStreakSerializer(streak).data)

    @action(detail=False, methods=["post"])
    def complete_weekly_review(self, request):
        review_date_raw = request.data.get("review_date")
        try:
            review_date = date.fromisoformat(review_date_raw) if review_date_raw else timezone.now().date()
        except (TypeError, ValueError):
            return Response({"detail": "review_date must be YYYY-MM-DD"}, status=400)

        result = award_points_for_weekly_review(request.user, window_start=review_date)
        serializer = self.get_serializer(result.event)
        return Response(serializer.data, status=201 if result.created else 200)

    @action(detail=False, methods=["post"])
    def complete_monthly_review(self, request):
        month_key = request.data.get("month")
        if month_key:
            try:
                month_key = date.fromisoformat(f"{month_key}-01").strftime("%Y-%m")
            except (TypeError, ValueError):
                return Response({"detail": "month must be YYYY-MM"}, status=400)
        else:
            month_key = timezone.now().strftime("%Y-%m")

        result = award_points_for_monthly_review(request.user, month_key=month_key)
        serializer = self.get_serializer(result.event)
        return Response(serializer.data, status=201 if result.created else 200)

    @action(detail=False, methods=["get"])
    def leaderboard(self, request):
        group_id = request.query_params.get("group_id")
        if not group_id:
            return Response({"detail": "group_id required"}, status=400)
        try:
            days = int(request.query_params.get("days", 7))
        except (TypeError, ValueError):
            return Response({"detail": "days must be an integer"}, status=400)
        if days <= 0 or days > 90:
            return Response({"detail": "days must be between 1 and 90"}, status=400)
        if not GroupMember.objects.filter(group_id=group_id, user=request.user).exists():
            return Response({"detail": "Not a member of this group"}, status=403)

        group = get_object_or_404(Group, id=group_id)
        payload = leaderboard_for_group(group=group, days=days)
        paginator = CircleLeaderboardPagination()
        page = paginator.paginate_queryset(payload["rows"], request, view=self)
        current_user_rank = next((row["rank"] for row in payload["rows"] if row["user_id"] == request.user.id), None)
        return Response(
            {
                "count": len(payload["rows"]),
                "next": paginator.get_next_link(),
                "previous": paginator.get_previous_link(),
                "group_id": group.id,
                "group_name": group.name,
                "days": payload["days"],
                "window_start": payload["start"].isoformat(),
                "window_end": payload["end"].isoformat(),
                "member_count": payload["member_count"],
                "current_user_rank": current_user_rank,
                "results": page,
            }
        )


class BadgeViewSet(ReadOnlyModelViewSet):
    serializer_class = BadgeSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return Badge.objects.filter(is_active=True).order_by("category", "name")


class UserBadgeViewSet(ReadOnlyModelViewSet):
    serializer_class = UserBadgeSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return UserBadge.objects.filter(user=self.request.user).select_related("badge", "trigger_event")
