from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from gamification.models import Badge, Group, GroupInvite, GroupInviteStatus, GroupMember, MonthlyTarget, PointAction, PointEvent, UserBadge, UserStreak
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

    def test_total_points_progression_updates_level(self):
        award_points(
            user=self.user,
            action=PointAction.WEEKLY_REVIEW,
            event_key="weekly_review:user:alice:2026-03-01",
        )
        award_points(
            user=self.user,
            action=PointAction.COMPLETE_MONTHLY_REVIEW,
            event_key="monthly_review:user:alice:2026-03",
        )

        streak = UserStreak.objects.get(user=self.user)
        self.assertEqual(streak.total_points_earned, 55)

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

    def test_backfilled_transaction_does_not_reset_current_streak(self):
        two_days_ago = timezone.now() - timedelta(days=2)
        yesterday = timezone.now() - timedelta(days=1)

        recent_txn = Transaction.objects.create(
            user=self.user,
            amount="20.00",
            currency="USD",
            direction="spend",
            occurred_at=yesterday,
            category="other",
            payment_channel="card",
        )
        latest_txn = Transaction.objects.create(
            user=self.user,
            amount="35.00",
            currency="USD",
            direction="spend",
            occurred_at=timezone.now(),
            category="other",
            payment_channel="card",
        )
        backfill_txn = Transaction.objects.create(
            user=self.user,
            amount="10.00",
            currency="USD",
            direction="spend",
            occurred_at=two_days_ago,
            category="other",
            payment_channel="card",
        )

        award_points_for_transaction(recent_txn)
        award_points_for_transaction(latest_txn)
        award_points_for_transaction(backfill_txn)

        streak = UserStreak.objects.get(user=self.user)
        self.assertEqual(streak.current_streak_days, 3)
        self.assertEqual(streak.best_streak_days, 3)
        self.assertEqual(streak.last_checkin_date, latest_txn.occurred_at.date())

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
        self.assertIn("first_log", badge_codes)
        self.assertIn("money_tracker_1", badge_codes)


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

    def test_group_invite_can_be_declined(self):
        newcomer = User.objects.create_user(username="charlie", password="pw")
        invite = GroupInvite.objects.create(
            group=self.group,
            invited_by=self.user,
            invited_user=newcomer,
            invite_code="invitecharlie",
        )

        newcomer_client = APIClient()
        newcomer_client.force_authenticate(newcomer)
        response = newcomer_client.post(f"/api/gamification/group-invites/{invite.id}/decline/")

        self.assertEqual(response.status_code, 200)
        invite.refresh_from_db()
        self.assertEqual(invite.status, GroupInviteStatus.DECLINED)

    def test_group_admin_can_list_members_and_promote_member(self):
        response = self.client.get(f"/api/gamification/groups/{self.group.id}/members/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json()), 2)

        membership = GroupMember.objects.get(group=self.group, user=self.peer)
        promote = self.client.post(
            f"/api/gamification/groups/{self.group.id}/update_member_role/",
            {"membership_id": membership.id, "role": "admin"},
            format="json",
        )

        self.assertEqual(promote.status_code, 200)
        membership.refresh_from_db()
        self.assertEqual(membership.role, "admin")

    def test_monthly_target_create_and_progress_complete(self):
        create = self.client.post(
            "/api/gamification/monthly-targets/",
            {
                "target_type": "transactions_logged",
                "title": "Log 10 purchases",
                "month_start": "2026-03-12",
                "target_value": 10,
            },
            format="json",
        )
        self.assertEqual(create.status_code, 201)
        target_id = create.json()["id"]
        target = MonthlyTarget.objects.get(id=target_id)
        self.assertEqual(str(target.month_start), "2026-03-01")

        progress = self.client.post(
            f"/api/gamification/monthly-targets/{target.id}/progress/",
            {"amount": 10},
            format="json",
        )
        self.assertEqual(progress.status_code, 200)
        target.refresh_from_db()
        self.assertEqual(target.status, "completed")
        self.assertEqual(
            PointEvent.objects.filter(user=self.user, action=PointAction.COMPLETE_MONTHLY_TARGET).count(),
            1,
        )

    def test_my_streak_returns_level_progression(self):
        award_points(
            user=self.user,
            action=PointAction.WEEKLY_REVIEW,
            event_key="weekly_review:user:alice:2026-03-04",
        )
        award_points(
            user=self.user,
            action=PointAction.COMPLETE_MONTHLY_REVIEW,
            event_key="monthly_review:user:alice:2026-03-04",
        )

        response = self.client.get("/api/gamification/points/my_streak/")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["total_points_earned"], 55)
        self.assertEqual(payload["level"], 2)
        self.assertEqual(payload["points_to_next_level"], 70)

    def test_weekly_review_endpoint_is_idempotent_within_same_week(self):
        first = self.client.post(
            "/api/gamification/points/complete_weekly_review/",
            {"review_date": "2026-03-18"},
            format="json",
        )
        second = self.client.post(
            "/api/gamification/points/complete_weekly_review/",
            {"review_date": "2026-03-20"},
            format="json",
        )

        self.assertEqual(first.status_code, 201)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(PointEvent.objects.filter(user=self.user, action=PointAction.WEEKLY_REVIEW).count(), 1)

    def test_monthly_review_endpoint_is_idempotent_within_same_month(self):
        first = self.client.post(
            "/api/gamification/points/complete_monthly_review/",
            {"month": "2026-03"},
            format="json",
        )
        second = self.client.post(
            "/api/gamification/points/complete_monthly_review/",
            {"month": "2026-03"},
            format="json",
        )

        self.assertEqual(first.status_code, 201)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(PointEvent.objects.filter(user=self.user, action=PointAction.COMPLETE_MONTHLY_REVIEW).count(), 1)

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
        rows = response.json()["results"]
        self.assertEqual(rows[0]["username"], "alice")
        self.assertEqual(rows[0]["points_total"], 25)
        self.assertEqual(rows[0]["reflections_count"], 1)
        self.assertEqual(rows[0]["rank"], 1)
        self.assertEqual(response.json()["current_user_rank"], 1)

    def test_leaderboard_uses_activity_window_date_not_insert_time(self):
        award_points(
            user=self.user,
            group=self.group,
            action=PointAction.LOG_PURCHASE,
            points=10,
            event_key="leaderboard:alice:current",
            window_date=timezone.now().date(),
        )
        PointEvent.objects.create(
            user=self.peer,
            group=self.group,
            action=PointAction.LOG_PURCHASE,
            points=999,
            window_date=timezone.now().date() - timedelta(days=30),
            metadata_json={},
        )

        response = self.client.get(f"/api/gamification/points/leaderboard/?group_id={self.group.id}&days=7")

        self.assertEqual(response.status_code, 200)
        rows = response.json()["results"]
        self.assertEqual(rows[0]["username"], "alice")
        self.assertEqual(rows[0]["points_total"], 10)
        self.assertEqual(rows[1]["username"], "bob")
        self.assertEqual(rows[1]["points_total"], 0)

    def test_leaderboard_is_paginated(self):
        for idx in range(12):
            member = User.objects.create_user(username=f"user{idx}", password="pw")
            GroupMember.objects.create(group=self.group, user=member, role="member")
            award_points(
                user=member,
                group=self.group,
                action=PointAction.LOG_PURCHASE,
                points=idx + 1,
                event_key=f"leaderboard:user:{idx}",
                window_date=timezone.now().date(),
            )

        response = self.client.get(
            f"/api/gamification/points/leaderboard/?group_id={self.group.id}&days=7&page=2&page_size=5"
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["count"], 14)
        self.assertEqual(len(payload["results"]), 5)
        self.assertIsNotNone(payload["next"])
        self.assertIsNotNone(payload["previous"])

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

        reflection_id = first.json()["id"]
        patch = self.client.patch(
            f"/api/transaction-reflections/{reflection_id}/",
            {"notes": "changed"},
            format="json",
        )
        delete = self.client.delete(f"/api/transaction-reflections/{reflection_id}/")

        self.assertEqual(patch.status_code, 405)
        self.assertEqual(delete.status_code, 405)

    def test_badges_endpoint_returns_catalog(self):
        response = self.client.get("/api/gamification/badges/")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(Badge.objects.exists())
