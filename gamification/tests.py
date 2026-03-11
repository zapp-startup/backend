from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from gamification.models import Badge, Group, GroupInvite, GroupInviteStatus, GroupMember, PointAction, PointEvent, UserBadge, UserStreak
from gamification.services import award_points, award_points_for_transaction, sync_badge_catalog
from subscriptions.models import Merchant, Subscription
from transactions.models import Transaction

User = get_user_model()


class GamificationServiceTests(TestCase):
    def setUp(self):
        sync_badge_catalog()
        self.user = User.objects.create_user(username="alice", password="pw")

    def test_award_points_is_idempotent_by_event_key(self):
        result_one = award_points(
            user=self.user,
            action=PointAction.WEEKLY_REVIEW,
            event_key="weekly_review:user:1:2026-03-11",
        )
        result_two = award_points(
            user=self.user,
            action=PointAction.WEEKLY_REVIEW,
            event_key="weekly_review:user:1:2026-03-11",
        )

        self.assertTrue(result_one.created)
        self.assertFalse(result_two.created)
        self.assertEqual(PointEvent.objects.count(), 1)

    def test_streak_only_increments_once_per_day(self):
        yesterday = timezone.now() - timedelta(days=1)
        today = timezone.now()

        txn_one = Transaction.objects.create(
            user=self.user,
            amount="20.00",
            currency="USD",
            direction="spend",
            occurred_at=yesterday,
            category="other",
            payment_channel="card",
        )
        txn_two = Transaction.objects.create(
            user=self.user,
            amount="35.00",
            currency="USD",
            direction="spend",
            occurred_at=today,
            category="other",
            payment_channel="card",
        )
        txn_three = Transaction.objects.create(
            user=self.user,
            amount="10.00",
            currency="USD",
            direction="spend",
            occurred_at=today,
            category="other",
            payment_channel="card",
        )

        award_points_for_transaction(txn_one)
        award_points_for_transaction(txn_two)
        award_points_for_transaction(txn_three)

        streak = UserStreak.objects.get(user=self.user)
        self.assertEqual(streak.current_streak_days, 2)
        self.assertEqual(streak.best_streak_days, 2)

    def test_purchase_badges_unlock_from_real_events(self):
        for idx in range(5):
            txn = Transaction.objects.create(
                user=self.user,
                amount="9.99",
                currency="USD",
                direction="spend",
                occurred_at=timezone.now() - timedelta(days=idx),
                category="other",
                payment_channel="card",
            )
            award_points_for_transaction(txn)

        badge_codes = set(UserBadge.objects.filter(user=self.user).values_list("badge__code", flat=True))
        self.assertIn("first_purchase", badge_codes)
        self.assertIn("purchase_5", badge_codes)


class GamificationApiTests(TestCase):
    def setUp(self):
        sync_badge_catalog()
        self.client = APIClient()
        self.user = User.objects.create_user(username="alice", password="pw")
        self.peer = User.objects.create_user(username="bob", password="pw")
        self.client.force_authenticate(self.user)
        self.group = Group.objects.create(name="Test Group", created_by=self.user, invite_code="invite123")
        GroupMember.objects.create(group=self.group, user=self.user, role="admin")
        GroupMember.objects.create(group=self.group, user=self.peer, role="member")

    def test_point_event_create_is_not_public(self):
        response = self.client.post("/api/gamification/points/", {"action": PointAction.LOG_PURCHASE, "points": 999}, format="json")
        self.assertEqual(response.status_code, 405)

    def test_group_join_is_idempotent(self):
        newcomer = User.objects.create_user(username="charlie", password="pw")
        client = APIClient()
        client.force_authenticate(newcomer)

        first = client.post("/api/gamification/groups/join/", {"invite_code": self.group.invite_code}, format="json")
        second = client.post("/api/gamification/groups/join/", {"invite_code": self.group.invite_code}, format="json")

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(GroupMember.objects.filter(group=self.group, user=newcomer).count(), 1)
        self.assertEqual(
            PointEvent.objects.filter(user=newcomer, action=PointAction.JOIN_GROUP, group=self.group).count(),
            1,
        )

    def test_group_admin_can_create_and_accept_targeted_invite(self):
        newcomer = User.objects.create_user(username="charlie", password="pw")

        create_response = self.client.post(
            "/api/gamification/group-invites/",
            {"group": self.group.id, "invited_user": newcomer.id, "note": "join us"},
            format="json",
        )

        self.assertEqual(create_response.status_code, 201)
        invite_id = create_response.json()["id"]
        invite = GroupInvite.objects.get(id=invite_id)
        self.assertEqual(invite.status, GroupInviteStatus.PENDING)

        newcomer_client = APIClient()
        newcomer_client.force_authenticate(newcomer)
        accept_response = newcomer_client.post(f"/api/gamification/group-invites/{invite.id}/accept/")

        self.assertEqual(accept_response.status_code, 200)
        invite.refresh_from_db()
        self.assertEqual(invite.status, GroupInviteStatus.ACCEPTED)
        self.assertEqual(invite.accepted_by, newcomer)
        self.assertTrue(GroupMember.objects.filter(group=self.group, user=newcomer).exists())

    def test_group_member_can_leave_and_owner_transfers(self):
        extra = User.objects.create_user(username="charlie", password="pw")
        GroupMember.objects.create(group=self.group, user=extra, role="member")

        response = self.client.post(f"/api/gamification/groups/{self.group.id}/leave/")

        self.assertEqual(response.status_code, 200)
        self.group.refresh_from_db()
        self.assertEqual(self.group.created_by, self.peer)
        self.assertFalse(GroupMember.objects.filter(group=self.group, user=self.user).exists())

    def test_group_admin_can_revoke_pending_invite(self):
        newcomer = User.objects.create_user(username="charlie", password="pw")
        invite = GroupInvite.objects.create(
            group=self.group,
            invited_by=self.user,
            invited_user=newcomer,
            invite_code="invitecharlie",
        )

        response = self.client.delete(f"/api/gamification/group-invites/{invite.id}/")

        self.assertEqual(response.status_code, 204)
        invite.refresh_from_db()
        self.assertEqual(invite.status, GroupInviteStatus.REVOKED)

    def test_leaderboard_aggregates_rolling_points(self):
        award_points(
            user=self.user,
            group=self.group,
            action=PointAction.LOG_PURCHASE,
            points=10,
            event_key="leaderboard:alice:1",
            window_date=timezone.now().date(),
        )
        award_points(
            user=self.user,
            group=self.group,
            action=PointAction.REFLECT_SAME_DAY,
            points=15,
            event_key="leaderboard:alice:2",
            window_date=timezone.now().date(),
        )
        award_points(
            user=self.peer,
            group=self.group,
            action=PointAction.LOG_PURCHASE,
            points=10,
            event_key="leaderboard:bob:1",
            window_date=timezone.now().date(),
        )

        response = self.client.get(f"/api/gamification/points/leaderboard/?group_id={self.group.id}&days=7")

        self.assertEqual(response.status_code, 200)
        rows = response.json()["rows"]
        self.assertEqual(rows[0]["username"], "alice")
        self.assertEqual(rows[0]["points_total"], 25)
        self.assertEqual(rows[0]["reflections_count"], 1)

    def test_subscription_status_changes_only_award_once(self):
        merchant = Merchant.objects.create(name="Netflix", category="streaming")
        subscription = Subscription.objects.create(
            user=self.user,
            merchant=merchant,
            price="14.99",
            currency="USD",
            status="active",
            billing_cycle="monthly",
        )

        response = self.client.patch(f"/api/subscriptions/{subscription.id}/", {"status": "paused"}, format="json")
        repeat = self.client.patch(f"/api/subscriptions/{subscription.id}/", {"status": "paused"}, format="json")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(repeat.status_code, 200)
        self.assertEqual(
            PointEvent.objects.filter(user=self.user, action=PointAction.PAUSE_SUBSCRIPTION, source_object_id=subscription.id).count(),
            1,
        )

    def test_transaction_reflection_prevents_duplicates_and_awards_once(self):
        transaction = Transaction.objects.create(
            user=self.user,
            amount="24.00",
            currency="USD",
            direction="spend",
            occurred_at=timezone.now(),
            category="other",
            payment_channel="card",
        )

        first = self.client.post(
            "/api/transaction-reflections/",
            {"transaction": transaction.id, "regret_score": 10, "was_worth_it": True, "notes": "solid"},
            format="json",
        )
        second = self.client.post(
            "/api/transaction-reflections/",
            {"transaction": transaction.id, "regret_score": 30, "was_worth_it": False, "notes": "duplicate"},
            format="json",
        )

        self.assertEqual(first.status_code, 201)
        self.assertEqual(second.status_code, 400)
        self.assertEqual(PointEvent.objects.filter(user=self.user, action=PointAction.REFLECT_SAME_DAY).count(), 1)

    def test_badges_endpoint_returns_catalog(self):
        response = self.client.get("/api/gamification/badges/")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(Badge.objects.exists())
