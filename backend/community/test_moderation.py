from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APITestCase

from .models import CommunityPost, ModerationJob, ModerationReview
from .moderation_checks import check, finding
from .moderation_worker import claim, finish, run_one


@override_settings(COMMUNITY_MODERATION_MODE='manual', COMMUNITY_POLICY_VERSION='community-v1')
class ModerationIntegrationTests(APITestCase):
    def setUp(self):
        self.owner = get_user_model().objects.create_user(username='moderation-owner')
        self.other = get_user_model().objects.create_user(username='moderation-other')
        self.staff = get_user_model().objects.create_user(username='moderation-staff', is_staff=True)
        self.client.force_authenticate(self.owner)

    def submit(self, key='submission', **changes):
        data = dict(board='free', teamCode='', category='잡담', title='한화 경기', content='wtf hanwha Eagles is at another level')
        data.update(changes)
        response = self.client.post('/api/v1/community/posts/', data, format='json', HTTP_IDEMPOTENCY_KEY=key)
        self.assertEqual(response.status_code, 202, response.data)
        return CommunityPost.objects.get(source_id=response.data['id'])

    def review(self, job, **changes):
        self.client.force_authenticate(self.staff)
        job.refresh_from_db()
        data = dict(revision=job.revision, review_revision=job.review_revision,
                    finding_ids=[f['id'] for f in job.findings], action='dismiss', note='원문과 정책을 확인했습니다.')
        data.update(changes)
        return self.client.post(f'/api/v1/community/admin/moderation/{job.pk}/review/', data, format='json')

    def test_pending_not_in_public_surfaces_and_owner_has_private_access(self):
        post = self.submit()
        self.assertEqual(self.client.get(f'/api/v1/community/submissions/posts/{post.pk}/').status_code, 200)
        for user in (None, self.other, self.staff):
            self.client.force_authenticate(user)
            self.assertNotIn(post.pk, [p['id'] for p in self.client.get('/api/v1/community/posts/').data])
            for suffix in ('', 'comments/', 'vote/'):
                self.assertNotEqual(self.client.get(f'/api/v1/community/posts/{post.pk}/{suffix}').status_code, 200)
            self.assertNotEqual(self.client.get(f'/api/v1/community/submissions/posts/{post.pk}/').status_code, 200)

    def test_manual_review_publish_then_edit_is_private_again(self):
        post = self.submit()
        run_one()
        job = post.moderation_jobs.get()
        post.refresh_from_db()
        self.assertEqual(post.moderation_status, 'review')
        self.assertEqual(self.review(job).status_code, 200)
        self.assertEqual(self.client.get(f'/api/v1/community/posts/{post.pk}/').status_code, 200)
        self.client.force_authenticate(self.owner)
        result = self.client.patch(f'/api/v1/community/posts/{post.pk}/', {'content': '수정된 응원'}, format='json')
        self.assertEqual(result.status_code, 202)
        self.assertEqual(result.data['revision'], 2)
        self.assertEqual(self.client.get(f'/api/v1/community/posts/{post.pk}/').status_code, 404)
        self.assertEqual(self.review(job).status_code, 409)

    def test_all_findings_must_be_resolved_and_confirmed_can_be_restored(self):
        post = self.submit()
        findings = [finding('political', '한화', '유사도'), finding('privacy', '경기', '연락처 확인')]
        run_one(lambda payload: (findings, {}))
        job = post.moderation_jobs.get()
        self.assertEqual(self.review(job, finding_ids=[findings[0]['id']]).status_code, 200)
        post.refresh_from_db()
        self.assertEqual(post.moderation_status, 'review')
        self.assertEqual(self.review(job, finding_ids=[findings[1]['id']], action='confirm').status_code, 200)
        post.refresh_from_db()
        self.assertEqual(post.moderation_status, 'rejected')
        self.assertEqual(self.review(job, finding_ids=[findings[1]['id']]).status_code, 200)
        post.refresh_from_db()
        self.assertEqual(post.moderation_status, 'approved')
        self.assertEqual(ModerationReview.objects.filter(job=job).count(), 3)

    def test_admin_manual_hide_is_not_overridden(self):
        post = self.submit()
        CommunityPost.objects.filter(pk=post.pk).update(is_hidden=True)
        run_one()
        self.assertEqual(self.review(post.moderation_jobs.get()).status_code, 200)
        post.refresh_from_db()
        self.assertTrue(post.is_hidden)
        self.assertIsNone(post.first_published_at)
        self.assertEqual(self.client.get(f'/api/v1/community/posts/{post.pk}/').status_code, 404)

    def test_review_requires_staff_and_current_review_revision(self):
        post = self.submit()
        run_one()
        job = post.moderation_jobs.get()
        self.assertEqual(self.client.get('/api/v1/community/admin/moderation/').status_code, 403)
        self.assertEqual(self.client.get(f'/api/v1/community/admin/posts/{post.pk}/').status_code, 403)
        self.assertEqual(self.client.post(f'/api/v1/community/admin/moderation/{job.pk}/review/', {}, format='json').status_code, 403)
        self.assertEqual(self.review(job).status_code, 200)
        preview = self.client.get(f'/api/v1/community/admin/posts/{post.pk}/')
        self.assertEqual(preview.status_code, 200)
        self.assertEqual(preview['Cache-Control'], 'private, no-store')
        self.assertEqual(self.review(job, review_revision=0).status_code, 409)

    def test_provider_failure_stays_private_and_retries(self):
        post = self.submit()
        def broken(payload):
            raise TimeoutError('sensitive input must not be saved')
        with override_settings(COMMUNITY_MODERATION_MAX_ATTEMPTS=1):
            run_one(broken)
        post.refresh_from_db()
        self.assertEqual(post.moderation_status, 'error')
        job = post.moderation_jobs.get()
        self.assertEqual(job.error_code, 'TimeoutError')
        self.assertEqual(self.client.get(f'/api/v1/community/posts/{post.pk}/').status_code, 404)
        self.client.force_authenticate(self.staff)
        result = self.client.post(f'/api/v1/community/admin/moderation/{job.pk}/retry/')
        self.assertEqual(result.status_code, 202)
        post.refresh_from_db()
        self.assertEqual(post.content_revision, 2)

    def test_duplicate_submission_does_not_enqueue_twice(self):
        post = self.submit(isTrade=True, isAdvertisement=True)
        result = self.client.post('/api/v1/community/posts/', dict(board='free', teamCode='', category='잡담', title='한화 경기', content='wtf hanwha Eagles is at another level', isTrade=True, isAdvertisement=True), format='json', HTTP_IDEMPOTENCY_KEY='submission')
        self.assertEqual(result.status_code, 200, result.data)
        self.assertEqual(post.moderation_jobs.count(), 1)

    def test_comment_is_not_public_or_counted_until_approved(self):
        post = self.submit()
        run_one(lambda payload: ([], {}))
        result = self.client.post(f'/api/v1/community/posts/{post.pk}/comments/', {'content': '정말 잘한다'}, format='json')
        self.assertEqual(result.status_code, 202, result.data)
        self.assertEqual(self.client.get(f'/api/v1/community/posts/{post.pk}/comments/').data, [])
        self.assertEqual(self.client.get(f'/api/v1/community/posts/{post.pk}/').data['commentCount'], 0)
        run_one(lambda payload: ([], {}))
        self.assertEqual(len(self.client.get(f'/api/v1/community/posts/{post.pk}/comments/').data), 1)
        self.assertEqual(self.client.get(f'/api/v1/community/posts/{post.pk}/').data['commentCount'], 1)

    def test_manual_checks_do_not_invent_semantic_classification(self):
        findings, evidence = check(dict(text='wtf 한화 미쳤다 유니폼 판매', is_trade=True, is_advertisement=True, image_ids=[], mode='manual', policy_version='community-v1'))
        self.assertEqual([item['code'] for item in findings], ['manual_review'])
        self.assertEqual(evidence['mode'], 'manual')

    def test_publication_points_are_awarded_once(self):
        post = self.submit()
        with patch('community.moderation_service.grant_points') as grant:
            run_one()
            job = post.moderation_jobs.get()
            self.review(job)
            self.review(job, action='confirm')
            self.review(job)
            self.assertEqual(grant.call_count, 1)

    def test_result_for_old_content_is_discarded(self):
        post = self.submit()
        old_job = claim()
        self.client.patch(f'/api/v1/community/posts/{post.pk}/', {'title': '새로운 내용'}, format='json')
        self.assertFalse(finish(old_job, result=([], {})))
        post.refresh_from_db()
        self.assertEqual((post.content_revision, post.moderation_status), (2, 'pending'))

    def test_expired_lease_is_reclaimed_and_old_token_is_fenced(self):
        post = self.submit()
        stale = claim()
        ModerationJob.objects.filter(pk=stale.pk).update(lease_until=timezone.now() - timedelta(seconds=1))
        replacement = claim()
        self.assertEqual(replacement.pk, stale.pk)
        self.assertNotEqual(replacement.lease_token, stale.lease_token)
        self.assertFalse(finish(stale, result=([], {})))
        self.assertTrue(finish(replacement, result=([], {})))
        post.refresh_from_db()
        self.assertEqual(post.moderation_status, 'approved')

    @override_settings(COMMUNITY_MODERATION_WRITES_PER_HOUR=1)
    def test_rate_limit_rolls_back_second_post(self):
        self.submit()
        result = self.client.post('/api/v1/community/posts/', dict(board='free', teamCode='', category='잡담', title='둘째', content='본문'), format='json', HTTP_IDEMPOTENCY_KEY='second')
        self.assertEqual(result.status_code, 429)
        self.assertEqual(CommunityPost.objects.filter(owner=self.owner).count(), 1)

    def test_chatbot_search_does_not_leak_pending_content(self):
        post = self.submit()
        from llm.tools.community import create_community_tools
        result = create_community_tools()[0].invoke({'query': 'at another level'})
        self.assertNotIn(post.pk, str(result))
        run_one(lambda payload: ([], {}))
        result = create_community_tools()[0].invoke({'query': 'at another level'})
        self.assertIn(post.pk, str(result))
