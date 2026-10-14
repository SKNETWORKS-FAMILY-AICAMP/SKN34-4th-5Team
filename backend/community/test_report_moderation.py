from unittest.mock import patch

from django.contrib.auth import get_user_model
from rest_framework.test import APITestCase
from django.db import transaction
from services.community_moderation import run_one, claim, finish

from .models import CommunityPost, CommunityReport, ModerationJob, ModerationSettings


class ReportModerationTests(APITestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="report-check")
        self.client.force_authenticate(self.user)
        self.post = CommunityPost.objects.create(
            post_number="088881", board="free", author="작성자", title="신고 재검사", content="야구 응원",
            category="잡담", moderation_status="approved", approved_revision=0, is_reviewed=True,
        )
        self.url = f"/api/v1/community/posts/{self.post.pk}/reports/"

    def report(self):
        return self.client.post(self.url, {"reason": "abuse", "detail": "확인 요청"}, format="json")

    def test_report_runs_targeted_openai_after_commit_and_duplicate_does_not_recheck(self):
        ModerationSettings.objects.update_or_create(pk=1, defaults={"mode": "manual"})
        with patch("services.community_moderation.run_one") as run:
            with self.captureOnCommitCallbacks(execute=True):
                response = self.report()
                run.assert_not_called()
            self.assertEqual(response.status_code, 201)
            job = ModerationJob.objects.get(post=self.post)
            run.assert_called_once_with(job_id=job.pk)
            self.assertEqual(job.payload["mode"], "openai")
            self.assertEqual(job.payload["trigger"], "report")
            self.post.refresh_from_db()
            self.assertFalse(self.post.is_reviewed)
            self.assertEqual(self.post.approved_revision, self.post.content_revision)
            with self.captureOnCommitCallbacks(execute=True):
                self.assertEqual(self.report().status_code, 200)
            self.assertEqual(run.call_count, 1)
            self.assertEqual(ModerationJob.objects.filter(post=self.post).count(), 1)
        self.assertEqual(ModerationSettings.objects.get(pk=1).mode, "manual")

    def test_report_result_uses_existing_publication_policy(self):
        for findings, expected in (([], "approved"), ([{"id": "harm", "code": "harm", "resolution": "open"}], "review")):
            with self.subTest(expected=expected):
                CommunityReport.objects.filter(post=self.post).delete()
                CommunityPost.objects.filter(pk=self.post.pk).update(moderation_status="approved", approved_revision=self.post.content_revision)
                with patch("services.community_moderation_policy.check", return_value=(findings, {"mode": "openai"})) as check:
                    with self.captureOnCommitCallbacks(execute=True):
                        self.assertEqual(self.report().status_code, 201)
                    check.assert_called_once()
                self.post.refresh_from_db()
                self.assertEqual(self.post.moderation_status, expected)
                self.assertEqual(self.post.approved_revision == self.post.content_revision, expected == "approved")
                self.assertFalse(self.post.is_hidden)

    def test_report_filter_and_default_priority(self):
        self.user.is_staff = True
        self.user.save(update_fields=["is_staff"])
        newer = CommunityPost.objects.create(post_number="088882", board="free", author="작성자", title="신고 재검사 최신", content="본문", category="잡담")
        CommunityReport.objects.create(post=self.post, reporter=self.user, reason="abuse")
        url = "/api/v1/community/admin/posts/"
        default = self.client.get(url, {"q": "신고 재검사"}).data["results"]
        self.assertEqual([row["source_id"] for row in default], [self.post.pk, newer.pk])
        reported = self.client.get(url, {"q": "신고 재검사", "filter": "reported"}).data
        self.assertEqual(reported["count"], 1)
        newest = self.client.get(url, {"q": "신고 재검사", "filter": "newest"}).data["results"]
        self.assertEqual(newest[0]["source_id"], newer.pk)
        self.assertEqual(self.client.get(url, {"filter": "invalid"}).status_code, 400)

    def test_different_reporters_share_inflight_job_and_targeted_claim_ignores_other_jobs(self):
        unrelated = ModerationJob.objects.create(post=CommunityPost.objects.create(post_number="088883", board="free", author="other", title="other", content="body", category="잡담"), revision=0, payload={})
        with self.captureOnCommitCallbacks(execute=False):
            self.assertEqual(self.report().status_code, 201)
        other = get_user_model().objects.create_user(username="second-reporter")
        self.client.force_authenticate(other)
        with self.captureOnCommitCallbacks(execute=False):
            self.assertEqual(self.report().status_code, 201)
        job = ModerationJob.objects.get(post=self.post)
        self.assertEqual(CommunityReport.objects.filter(post=self.post).count(), 2)
        with patch("services.community_moderation_policy.check", return_value=([], {})) as checker:
            self.assertTrue(run_one(job_id=job.pk))
            self.assertFalse(run_one(job_id=job.pk))
            checker.assert_called_once()
        unrelated.refresh_from_db()
        self.assertEqual(unrelated.state, "queued")

    def test_provider_failure_retains_manual_fallback_and_report(self):
        with patch("services.community_moderation_policy.check", side_effect=TimeoutError("private diagnostic")):
            with self.captureOnCommitCallbacks(execute=True):
                self.assertEqual(self.report().status_code, 201)
        self.post.refresh_from_db()
        job = ModerationJob.objects.get(post=self.post)
        self.assertEqual(job.error_code, "automatic_check_failed")
        self.assertEqual(self.post.moderation_status, "review")
        self.assertEqual(self.post.approved_revision, self.post.content_revision)
        self.assertEqual(self.post.reports.get().status, "pending")
        self.assertNotIn("private diagnostic", str(job.evidence))

    def test_report_rollback_does_not_call_provider_or_leave_a_job(self):
        with patch("services.community_moderation.run_one") as run:
            with self.captureOnCommitCallbacks(execute=True):
                with transaction.atomic():
                    self.report()
                    transaction.set_rollback(True)
            run.assert_not_called()
        self.assertFalse(ModerationJob.objects.filter(post=self.post).exists())
        self.assertFalse(self.post.reports.exists())

    def test_hidden_posts_reject_new_reports_but_existing_report_retry_is_idempotent(self):
        CommunityReport.objects.create(post=self.post, reporter=self.user, reason="abuse")
        CommunityPost.objects.filter(pk=self.post.pk).update(is_hidden=True)
        with patch("services.community_moderation.run_one") as run:
            with self.captureOnCommitCallbacks(execute=True):
                self.assertEqual(self.report().status_code, 200)
                self.client.force_authenticate(get_user_model().objects.create_user(username="no-private-access"))
                self.assertEqual(self.report().status_code, 404)
            run.assert_not_called()

    def test_late_report_result_does_not_override_new_revision_or_admin_hide(self):
        with self.captureOnCommitCallbacks(execute=False):
            self.report()
        job = claim(job_id=ModerationJob.objects.get(post=self.post).pk)
        self.post.refresh_from_db()
        CommunityPost.objects.filter(pk=self.post.pk).update(content_revision=self.post.content_revision + 1, is_hidden=True)
        finish(job, result=([], {}))
        job.refresh_from_db()
        self.post.refresh_from_db()
        self.assertEqual(job.state, "cancelled")
        self.assertTrue(self.post.is_hidden)
