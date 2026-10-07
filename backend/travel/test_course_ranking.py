from datetime import timedelta

from django.utils import timezone
from rest_framework.test import APITestCase

from .models import Course


class CourseRankingTests(APITestCase):
    def setUp(self):
        Course.objects.all().delete()

    def course(self, likes=0, stadium="사직야구장", **kwargs):
        return Course.objects.create(title="직관 코스", stadium=stadium, duration="반나절",
                                     edit_token_hash="unused", likes=likes, **kwargs)

    def best(self, **kwargs):
        return self.client.get("/api/v1/courses/", {
            "ordering": "likes", "limit": "5", "exclude_samples": "true", **kwargs,
        })

    def test_public_ranking_filters_before_limit(self):
        for likes in range(8):
            self.course(likes)
        self.course(900, is_sample=True)
        self.course(800, stadium="잠실야구장")
        response = self.best(stadium="SAJIK")
        self.assertEqual(response.status_code, 200)
        self.assertEqual([row["likes"] for row in response.data], [7, 6, 5, 4, 3])
        self.assertTrue(all(not row["isSample"] for row in response.data))
        self.assertEqual(self.best().data[0]["stadium"], "잠실야구장")

    def test_ties_empty_and_fewer_than_five(self):
        self.assertEqual(self.best().data, [])
        old, new = self.course(2), self.course(2)
        Course.objects.filter(pk=old.pk).update(created_at=timezone.now() - timedelta(days=1))
        self.assertEqual([row["id"] for row in self.best().data], [str(new.pk), str(old.pk)])
        Course.objects.filter(pk=old.pk).update(created_at=new.created_at)
        expected = [str(pk) for pk in sorted([old.pk, new.pk], reverse=True)]
        self.assertEqual([row["id"] for row in self.best().data], expected)

    def test_aliases_and_legacy_list_compatibility(self):
        for name in ("문학야구장", "인천 SSG 랜더스필드", "MUNHAK"):
            self.course(stadium=name)
        self.course(is_sample=True)
        self.assertEqual(len(self.best(stadium="MUNHAK").data), 3)
        response = self.client.get("/api/v1/courses/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 4)

    def test_invalid_queries(self):
        for query in ({"stadium": "invalid"}, {"ordering": "title"}, {"limit": "0"},
                      {"limit": "101"}, {"limit": "1.5"}, {"exclude_samples": "yes"}):
            with self.subTest(query=query):
                self.assertEqual(self.best(**query).status_code, 400)
        self.assertEqual(self.client.get("/api/v1/courses/?limit=5&limit=10").status_code, 400)

    def test_reaction_changes_next_ranking_without_touching_other_courses(self):
        from django.contrib.auth import get_user_model
        low, high = self.course(), self.course(1)
        self.client.force_authenticate(get_user_model().objects.create_user(username="ranking-user"))
        url = f"/api/v1/courses/{low.pk}/reaction/"
        self.assertEqual(self.client.post(url, {"liked": True}, format="json").status_code, 200)
        low.refresh_from_db()
        self.assertEqual(low.likes, 1)
        self.assertEqual(self.client.post(url, {"liked": False}, format="json").status_code, 200)
        self.assertEqual(self.best().data[0]["id"], str(high.pk))
