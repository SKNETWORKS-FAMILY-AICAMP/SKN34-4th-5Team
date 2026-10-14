from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from accounts.models import PointTransaction
from accounts.point_policy import MEMBER_RANK_THRESHOLDS
from accounts.point_rank import member_rank, rank_for_points
from accounts.point_service import adjust_points, grant_points


class PointRankTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="rank-user", nickname="야구팬")

    def test_rank_thresholds_cover_zero_through_ten_thousand(self):
        cases = ((0, 1), (999, 1), (1000, 2), (2499, 2), (2500, 3),
                 (4999, 3), (5000, 4), (7499, 4), (7500, 5), (10000, 5), (10001, 5))
        for points, expected in cases:
            with self.subTest(points=points):
                self.assertEqual(rank_for_points(points), expected)
        self.assertEqual(MEMBER_RANK_THRESHOLDS, (1000, 2500, 5000, 7500))

    def test_rewards_count_but_spending_refunds_and_admin_adjustments_do_not(self):
        wallet, earned, created = grant_points(
            user=self.user, amount=100, source_type="activity.post", source_key="1",
        )
        self.assertTrue(created)
        self.assertEqual((wallet.balance, wallet.total_earned, earned.rank_delta), (100, 100, 100))

        wallet, spent, _ = adjust_points(
            user=self.user, amount=-40, transaction_type="SPEND",
            source_type="point.spend", source_key="1",
        )
        self.assertEqual((wallet.balance, wallet.total_earned, spent.rank_delta), (60, 100, 0))
        wallet, refund, _ = adjust_points(
            user=self.user, amount=40, transaction_type="REFUND",
            source_type="point.refund", source_key="1",
        )
        self.assertEqual((wallet.balance, wallet.total_earned, refund.rank_delta), (100, 100, 0))
        wallet, admin, _ = adjust_points(
            user=self.user, amount=25, transaction_type="ADMIN_ADJUSTMENT",
            source_type="admin.adjustment", source_key="1",
        )
        self.assertEqual((wallet.balance, wallet.total_earned, admin.rank_delta), (125, 100, 0))

        wallet, correction, _ = adjust_points(
            user=self.user, amount=-20, rank_delta=-20,
            transaction_type=PointTransaction.ACTIVITY_EARNED,
            source_type="activity.reversal", source_key="1",
        )
        self.user.refresh_from_db()
        self.assertEqual((wallet.balance, wallet.total_earned, correction.rank_delta), (105, 80, -20))
        self.assertEqual(member_rank(self.user), 1)

    def test_adjustments_are_idempotent_and_cannot_make_balances_negative(self):
        arguments = dict(
            user=self.user, amount=50, transaction_type="ADMIN_ADJUSTMENT",
            source_type="admin.adjustment", source_key="same",
        )
        wallet, first, created = adjust_points(**arguments)
        same_wallet, same_entry, duplicate = adjust_points(**arguments)
        self.assertTrue(created)
        self.assertFalse(duplicate)
        self.assertEqual((wallet.pk, first.pk), (same_wallet.pk, same_entry.pk))
        with self.assertRaises(ValueError):
            adjust_points(
                user=self.user, amount=-51, transaction_type="SPEND",
                source_type="point.spend", source_key="too-much",
            )
        with self.assertRaises(ValueError):
            adjust_points(
                user=self.user, amount=-1, rank_delta=-1, transaction_type="REVERSAL",
                source_type="point.reversal", source_key="too-much",
            )
        wallet.refresh_from_db()
        self.assertEqual((wallet.balance, wallet.total_earned), (50, 0))

    def test_member_and_public_responses_return_rank_without_point_totals(self):
        client = APIClient()
        client.force_authenticate(self.user)
        self.assertEqual(client.get("/api/v1/auth/user").data["rank"], 1)
        grant_points(user=self.user, amount=1000, source_type="activity.post", source_key="rank")
        self.user.refresh_from_db()

        member = client.get("/api/v1/auth/user").data
        public = client.get(f"/api/v1/auth/users/{self.user.pk}/public/").data
        self.assertEqual(member["rank"], 2)
        self.assertEqual(public["rank"], 2)
        for response in (member, public):
            self.assertNotIn("balance", response)
            self.assertNotIn("total_earned", response)
