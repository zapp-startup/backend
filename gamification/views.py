import secrets
from datetime import timedelta

from django.db import transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.viewsets import ModelViewSet, ReadOnlyModelViewSet

from .models import Badge, Group, GroupInvite, GroupInviteStatus, GroupMember, GroupRole, PointEvent, UserBadge, UserStreak
from .serializers import (
    BadgeSerializer,
    GroupInviteSerializer,
    GroupSerializer,
    PointEventSerializer,
    UserBadgeSerializer,
    UserStreakSerializer,
)
from .services import award_points_for_group_create, award_points_for_group_join, leaderboard_for_group


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


class GroupViewSet(ModelViewSet):
    serializer_class = GroupSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return Group.objects.filter(memberships__user=self.request.user).distinct()

    def perform_create(self, serializer):
        invite_code = secrets.token_hex(4)
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


class PointEventViewSet(ReadOnlyModelViewSet):
    serializer_class = PointEventSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return PointEvent.objects.filter(user=self.request.user).select_related("group")

    @action(detail=False, methods=["get"])
    def my_streak(self, request):
        streak, _ = UserStreak.objects.get_or_create(user=request.user)
        return Response(UserStreakSerializer(streak).data)

    @action(detail=False, methods=["get"])
    def leaderboard(self, request):
        group_id = request.query_params.get("group_id")
        days = int(request.query_params.get("days", 7))
        if not group_id:
            return Response({"detail": "group_id required"}, status=400)
        if days <= 0 or days > 90:
            return Response({"detail": "days must be between 1 and 90"}, status=400)
        if not GroupMember.objects.filter(group_id=group_id, user=request.user).exists():
            return Response({"detail": "Not a member of this group"}, status=403)

        group = Group.objects.get(id=group_id)
        return Response(leaderboard_for_group(group=group, days=days))


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
