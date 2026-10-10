from io import StringIO
from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import CustomUser, PointWallet
from community.models import CommunityPost


class PointIntegrationTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = CustomUser.objects.create_user(username="testuser1", password="password123!")
        self.user2 = CustomUser.objects.create_user(username="testuser2", password="password123!")

    def test_signup_grants_points(self):
        response = self.client.post("/api/v1/auth/signup/", {
            "username": "newuser",
            "password": "Password123!",
            "re_password": "Password123!",
            "nickname": "newuser",
            "email": "newuser@example.com",
            "birth_date": "2000-01-01",
            "first_name": "홍길동",
            "gender": "M",
            "team_code": ""
        }, format="json")
        if response.status_code != 201:
            print(response.data)
        self.assertEqual(response.status_code, 201)
        new_user = CustomUser.objects.get(username="newuser")
        wallet = PointWallet.objects.get(user=new_user)
        self.assertEqual(wallet.balance, 100)

    def test_login_grants_daily_points(self):
        self.assertEqual(PointWallet.objects.filter(user=self.user).count(), 0)
        
        response = self.client.post("/api/v1/auth/signin", {
            "username": "testuser1",
            "password": "password123!"
        })
        self.assertEqual(response.status_code, 200)
        
        wallet = PointWallet.objects.get(user=self.user)
        self.assertEqual(wallet.balance, 10)
        
        # Second login same day should not grant more points
        self.client.post("/api/v1/auth/signin", {
            "username": "testuser1",
            "password": "password123!"
        })
        wallet.refresh_from_db()
        self.assertEqual(wallet.balance, 10)

    def test_post_creation_grants_points(self):
        self.client.force_authenticate(user=self.user)
        response = self.client.post("/api/v1/community/posts/", {
            "title": "Test Post",
            "content": "Test Content",
            "board": "free",
            "category": "잡담",
            "teamCode": "",
            "images": []
        }, format="json", HTTP_IDEMPOTENCY_KEY="test-key-1")
        if response.status_code != 201:
            print("Post error:", response.data)
        self.assertEqual(response.status_code, 201)
        
        wallet = PointWallet.objects.get(user=self.user)
        self.assertEqual(wallet.balance, 1)

    def test_post_read_grants_points(self):
        post = CommunityPost.objects.create(
            owner=self.user2,
            author="testuser2",
            title="Read me",
            content="Content",
            board="free",
            category="잡담",
            team_code="",
            source_id="read-1"
        )
        self.client.force_authenticate(user=self.user)
        
        # First read by testuser1
        response = self.client.get(f"/api/v1/community/posts/{post.pk}/")
        self.assertEqual(response.status_code, 200)
        wallet = PointWallet.objects.get(user=self.user)
        self.assertEqual(wallet.balance, 1)
        
        # Second read by testuser1
        self.client.get(f"/api/v1/community/posts/{post.pk}/")
        wallet.refresh_from_db()
        self.assertEqual(wallet.balance, 1)  # Balance should not increase
        
        # Read by owner (testuser2)
        self.client.force_authenticate(user=self.user2)
        self.client.get(f"/api/v1/community/posts/{post.pk}/")
        wallet2 = PointWallet.objects.filter(user=self.user2).first()
        self.assertIsNone(wallet2)  # Owner gets no point for their own post

    def test_comment_creation_grants_points(self):
        post = CommunityPost.objects.create(
            owner=self.user2,
            author="testuser2",
            title="Comment me",
            content="Content",
            board="free",
            category="잡담",
            team_code="",
            source_id="comment-1"
        )
        self.client.force_authenticate(user=self.user)
        response = self.client.post(f"/api/v1/community/posts/{post.pk}/comments/", {
            "content": "Test comment"
        })
        self.assertEqual(response.status_code, 201)
        wallet = PointWallet.objects.get(user=self.user)
        self.assertEqual(wallet.balance, 1)

    def test_backfill_signup_points_command(self):
        out = StringIO()
        call_command("backfill_signup_points", dry_run=True, stdout=out)
        self.assertIn("대상 회원:", out.getvalue())
        
        call_command("backfill_signup_points")
        self.assertEqual(PointWallet.objects.get(user=self.user).balance, 100)
        self.assertEqual(PointWallet.objects.get(user=self.user2).balance, 100)
        
        # Calling again should be idempotent
        call_command("backfill_signup_points")
        self.assertEqual(PointWallet.objects.get(user=self.user).balance, 100)

    def test_point_wallet_api(self):
        PointWallet.objects.create(user=self.user, balance=500)
        self.client.force_authenticate(user=self.user)
        response = self.client.get("/api/v1/fantasy/points/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["balance"], 500)
