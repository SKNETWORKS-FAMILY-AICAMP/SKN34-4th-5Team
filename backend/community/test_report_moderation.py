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

    def prior_job(self, code='manual_review', resolution='open', fallback=True):
        from services.community_moderation_policy import finding
        self.post.content_revision = 1
        self.post.approved_revision = 1 if fallback or resolution == 'dismissed' else None
        self.post.moderation_status = 'review'
        self.post.save()
        item = finding(code, '', '기존 운영진 검토 사유')
        item['resolution'] = resolution
        return ModerationJob.objects.create(
            post=self.post, revision=1, state='done', findings=[item],
            payload={'mode': 'openai'}, error_code='automatic_check_failed' if fallback else '',
            evidence={'fallback': 'manual_review'} if fallback else {},
        )

    def enqueue_report(self):
        with self.captureOnCommitCallbacks(execute=False):
            self.assertEqual(self.report().status_code, 201)
        return self.post.moderation_jobs.get(payload__trigger='report')

    def review(self, job, action):
        self.user.is_staff = True
        self.user.save(update_fields=['is_staff'])
        response = self.client.post(f'/api/v1/community/admin/moderation/{job.pk}/review/', {
            'revision': job.revision, 'review_revision': job.review_revision,
            'finding_ids': [item['id'] for item in job.findings], 'action': action, 'note': '운영진 확인',
        }, format='json')
        self.assertEqual(response.status_code, 200)
        job.refresh_from_db()
        self.post.refresh_from_db()

    def test_report_preserves_fallback_list_review_and_publication_after_clean_finish(self):
        self.post.owner = self.user
        self.post.save(update_fields=['owner'])
        old = self.prior_job()
        with patch('community.moderation_service.grant_points') as grant:
            new = self.enqueue_report()
            self.post.refresh_from_db()
            self.assertEqual(self.post.content_revision, 1)
            ref = claim(job_id=new.pk)
            self.assertTrue(finish(ref, result=([], {})))
            self.user.is_staff = True
            self.user.save(update_fields=['is_staff'])
            listed = self.client.get('/api/v1/community/admin/moderation/').data['results']
            self.assertIn(old.pk, [item['id'] for item in listed])
            self.post.refresh_from_db()
            self.assertEqual(self.post.moderation_status, 'review')
            self.assertEqual(self.post.approved_revision, 1)
            self.review(old, 'confirm')
            self.assertEqual(self.post.moderation_status, 'rejected')
            self.assertIsNone(self.post.approved_revision)
            self.review(old, 'dismiss')
            self.assertEqual(self.post.moderation_status, 'approved')
            self.assertEqual(self.post.approved_revision, 1)
            self.assertEqual(old.reviews.count(), 2)
            self.assertEqual(grant.call_count, 1)

    def test_review_while_report_running_survives_finish_and_hide(self):
        old = self.prior_job()
        new = self.enqueue_report()
        ref = claim(job_id=new.pk)
        self.review(old, 'confirm')
        CommunityPost.objects.filter(pk=self.post.pk).update(is_hidden=True)
        self.assertTrue(finish(ref, result=([], {})))
        self.post.refresh_from_db()
        self.assertEqual(self.post.moderation_status, 'rejected')
        self.assertIsNone(self.post.approved_revision)
        self.assertTrue(self.post.is_hidden)
        self.review(old, 'dismiss')
        self.assertEqual(self.post.approved_revision, 1)
        self.assertTrue(self.post.is_hidden)
        old.refresh_from_db()
        self.assertEqual(old.findings[0]['resolution'], 'dismissed')
        self.assertEqual(old.reviews.count(), 2)

    def test_clean_report_cannot_clear_explicit_manual_or_confirmed_reason(self):
        for resolution in ('open', 'confirmed'):
            with self.subTest(resolution=resolution):
                self.post.moderation_jobs.all().delete()
                self.post.reports.all().delete()
                old = self.prior_job()
                new = self.enqueue_report()
                # The report was accepted while public; the other decision arrives first.
                old.error_code = ''
                old.evidence = {}
                old.findings[0]['resolution'] = resolution
                old.save()
                from .moderation_service import apply_decision_locked
                with transaction.atomic():
                    apply_decision_locked(self.post, old)
                self.assertTrue(finish(claim(job_id=new.pk), result=([], {})))
                self.post.refresh_from_db()
                self.assertIsNone(self.post.approved_revision)
                self.assertEqual(self.post.moderation_status, 'review' if resolution == 'open' else 'rejected')
                self.review(old, 'dismiss')
                self.assertEqual(self.post.approved_revision, 1)

    def test_report_keeps_content_check_lease_and_combines_both_finish_orders(self):
        for report_first in (False, True):
            with self.subTest(report_first=report_first):
                self.post.moderation_jobs.all().delete()
                self.post.reports.all().delete()
                content_job = self.prior_job()
                content_job.state = 'queued'
                content_job.findings = []
                content_job.save()
                content_ref = claim(job_id=content_job.pk)
                new = self.enqueue_report()
                report_ref = claim(job_id=new.pk)
                harm = {'id': 'harm', 'code': 'harm', 'resolution': 'open'}
                calls = [(content_ref, ([harm], {})), (report_ref, ([], {}))]
                if report_first:
                    calls.reverse()
                for ref, result in calls:
                    self.assertTrue(finish(ref, result=result))
                self.post.refresh_from_db()
                self.assertIsNone(self.post.approved_revision)
                content_job.refresh_from_db()
                self.review(content_job, 'dismiss')
                self.assertEqual(self.post.approved_revision, 1)
                self.assertEqual(self.post.moderation_status, 'approved')
                self.assertEqual(self.post.content_revision, 1)

    def test_new_image_reason_blocks_despite_prior_dismissed_image_history(self):
        from .models import CommunityImage
        from services.community_moderation_policy import finding
        old = self.prior_job(code='image_review', resolution='dismissed', fallback=False)
        CommunityImage.objects.create(post=self.post, object_key='report-image', content_type='image/png',
                                      size=1, width=1, height=1)
        new = self.enqueue_report()
        self.assertEqual(len(new.payload['image_ids']), 1)
        self.assertTrue(finish(claim(job_id=new.pk), result=([finding('image_review', '', '새 이미지 검사')], {})))
        self.post.refresh_from_db()
        self.assertIsNone(self.post.approved_revision)
        old.refresh_from_db()
        self.assertEqual(old.findings[0]['resolution'], 'dismissed')
        new.refresh_from_db()
        self.review(new, 'dismiss')
        self.assertEqual(self.post.approved_revision, 1)

    def test_multiple_completed_reports_keep_each_review_and_original_fallback_scope(self):
        old = self.prior_job()
        first = self.enqueue_report()
        self.assertTrue(finish(claim(job_id=first.pk), error=TimeoutError()))
        other = get_user_model().objects.create_user(username='later-reporter')
        self.client.force_authenticate(other)
        with self.captureOnCommitCallbacks(execute=False):
            self.assertEqual(self.report().status_code, 201)
        second = self.post.moderation_jobs.filter(payload__trigger='report').latest('pk')
        self.assertTrue(finish(claim(job_id=second.pk), result=([], {})))
        self.assertEqual(self.post.moderation_jobs.filter(revision=1, state='done').count(), 3)
        self.client.force_authenticate(self.user)
        first.refresh_from_db()
        self.review(old, 'dismiss')
        self.assertEqual(self.post.moderation_status, 'review')
        self.review(first, 'confirm')
        self.assertIsNone(self.post.approved_revision)
        self.review(first, 'dismiss')
        self.assertEqual(self.post.moderation_status, 'approved')
        self.assertEqual(self.post.approved_revision, 1)
        # A fallback result must not exempt an explicit manual_review from another job.
        old.error_code = ''
        old.evidence = {}
        old.findings[0]['resolution'] = 'open'
        old.save()
        self.post.moderation_jobs.filter(pk=first.pk).update(findings=[], state='queued')
        self.assertTrue(finish(claim(job_id=first.pk), error=TimeoutError()))
        self.post.refresh_from_db()
        self.assertIsNone(self.post.approved_revision)
        self.assertEqual(self.post.moderation_status, 'review')

    def test_content_job_uniqueness_includes_missing_null_and_non_report_triggers(self):
        from django.db import IntegrityError
        from .models import CommunityComment
        for trigger in ({}, {'trigger': None}, {'trigger': 'content'}):
            with self.subTest(payload=trigger):
                self.post.moderation_jobs.all().delete()
                self.post.moderation_jobs.create(revision=1, payload=trigger)
                with self.assertRaises(IntegrityError), transaction.atomic():
                    self.post.moderation_jobs.create(revision=1, payload={})
                self.post.moderation_jobs.create(revision=1, payload={'trigger': 'report'})
                self.post.moderation_jobs.create(revision=1, payload={'trigger': 'report'})
        comment = CommunityComment.objects.create(post=self.post, author=self.user, content='댓글')
        comment.moderation_jobs.create(revision=1, payload={'trigger': 'report'})
        with self.assertRaises(IntegrityError), transaction.atomic():
            comment.moderation_jobs.create(revision=1, payload={'trigger': 'report'})

    def test_actual_content_edit_cancels_report_lease_and_stales_old_review(self):
        old = self.prior_job()
        self.post.owner = self.user
        self.post.save(update_fields=['owner'])
        new = self.enqueue_report()
        ref = claim(job_id=new.pk)
        with self.captureOnCommitCallbacks(execute=False):
            response = self.client.patch(f'/api/v1/community/posts/{self.post.pk}/',
                                         {'content': '실제 본문 수정'}, format='json')
        self.assertEqual(response.status_code, 202)
        self.post.refresh_from_db()
        self.assertEqual(self.post.content_revision, 2)
        self.assertFalse(finish(ref, result=([], {})))
        new.refresh_from_db()
        self.assertEqual(new.state, 'cancelled')
        self.user.is_staff = True
        self.user.save(update_fields=['is_staff'])
        response = self.client.post(f'/api/v1/community/admin/moderation/{old.pk}/review/', {
            'revision': 1, 'review_revision': 0, 'finding_ids': [old.findings[0]['id']],
            'action': 'confirm', 'note': '이전 본문',
        }, format='json')
        self.assertEqual(response.status_code, 409)
        listed = self.client.get('/api/v1/community/admin/moderation/').data['results']
        self.assertNotIn(old.pk, [item['id'] for item in listed])

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
