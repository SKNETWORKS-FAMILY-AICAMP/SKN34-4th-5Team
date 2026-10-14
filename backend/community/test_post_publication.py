from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import transaction
from django.test import override_settings
from rest_framework.test import APITestCase

from .models import CommunityPost, CommunityComment, CommunityDraft, ModerationSettings
from .moderation_checks import finding
from .moderation_service import enqueue_locked, retry_job, review_job
from .moderation_worker import claim, finish, run_one
from services.community_moderation import enqueue_unchecked


class PostPublicationTests(APITestCase):
    def setUp(self):
        self.owner = get_user_model().objects.create_user(username='publication-owner')
        self.staff = get_user_model().objects.create_user(username='publication-staff', is_staff=True)
        ModerationSettings.objects.create(mode='manual')
        self.client.force_authenticate(self.owner)

    def submit(self):
        response = self.client.post('/api/v1/community/posts/', dict(
            board='free', teamCode='', category='잡담', title='즉시 공개', content='야구 응원',
        ), format='json', HTTP_IDEMPOTENCY_KEY='publication')
        return response, CommunityPost.objects.get(pk=response.data['id'])

    def assert_public(self, post):
        self.client.force_authenticate(None)
        self.assertEqual(self.client.get(f'/api/v1/community/posts/{post.pk}/').status_code, 200)
        self.assertIn(post.pk, [row['id'] for row in self.client.get('/api/v1/community/posts/').data])
        self.client.force_authenticate(self.owner)

    def test_create_is_public_without_provider_and_idempotent_points(self):
        with patch('services.community_moderation_policy.check') as provider, \
                patch('community.moderation_service.grant_points') as points:
            response, post = self.submit()
            self.assertEqual(response.status_code, 201)
            self.assertEqual(response.data['moderationStatus'], 'pending')
            self.assert_public(post)
            retry, _ = self.submit()
            self.assertEqual(retry.status_code, 200)
            self.assertEqual(post.moderation_jobs.count(), 1)
            self.assertEqual(points.call_count, 1)
            provider.assert_not_called()

    def test_comment_create_and_edit_are_public_and_counted_before_check(self):
        _, post = self.submit()
        with patch('services.community_moderation_policy.check') as provider, \
                patch('community.moderation_service.grant_points') as points:
            created = self.client.post(f'/api/v1/community/posts/{post.pk}/comments/', {'content': '응원'}, format='json')
            self.assertEqual(created.status_code, 201)
            edited = self.client.patch(f"/api/v1/community/comments/{created.data['id']}/", {'content': '수정 응원'}, format='json')
            self.assertEqual(edited.status_code, 200)
            self.client.force_authenticate(None)
            comments = self.client.get(f'/api/v1/community/posts/{post.pk}/comments/').data
            self.assertEqual([row['content'] for row in comments], ['수정 응원'])
            self.assertEqual(self.client.get(f'/api/v1/community/posts/{post.pk}/').data['commentCount'], 1)
            self.assertEqual(points.call_count, 1)
            provider.assert_not_called()

    def test_draft_publish_is_public_before_check(self):
        draft = CommunityDraft.objects.create(owner=self.owner, board='free', category='잡담', title='초안', content='초안 응원')
        with patch('services.community_moderation_policy.check') as provider:
            response = self.client.post(f'/api/v1/community/drafts/{draft.pk}/publish/', {'revision': draft.revision}, format='json', HTTP_IDEMPOTENCY_KEY='draft-publication')
            self.assertEqual(response.status_code, 201, response.data)
            self.assert_public(CommunityPost.objects.get(pk=response.data['id']))
            provider.assert_not_called()

    def test_edit_is_public_and_stale_completion_cannot_hide_it(self):
        _, post = self.submit()
        old = claim()
        with patch('services.community_moderation_policy.check') as provider:
            response = self.client.patch(f'/api/v1/community/posts/{post.pk}/', {'content': '새 내용'}, format='json')
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.data['revision'], 2)
            provider.assert_not_called()
        self.assertFalse(finish(old, result=([finding('privacy', '', '과거 내용')], {})))
        self.assert_public(post)

    def test_routine_preserves_revision_visibility_and_hidden_state(self):
        post = CommunityPost.objects.create(owner=self.owner, board='free', category='잡담', title='기존', content='공개', approved_revision=0)
        comment = CommunityComment.objects.create(post=post, author=self.owner, content='기존 댓글', approved_revision=0)
        hidden = CommunityPost.objects.create(owner=self.owner, board='free', category='잡담', title='숨김', content='제재', approved_revision=0, is_hidden=True)
        counts = enqueue_unchecked()
        self.assertGreaterEqual(counts['posts'], 2)  # Seed migrations also contain legacy posts.
        self.assertEqual(counts['comments'], 1)
        post.refresh_from_db(); comment.refresh_from_db(); hidden.refresh_from_db()
        self.assertEqual((post.content_revision, post.approved_revision, comment.content_revision, comment.approved_revision), (0, 0, 0, 0))
        self.assertTrue(hidden.is_hidden)
        self.assert_public(post)
        self.assertEqual(enqueue_unchecked(), {'posts': 0, 'comments': 0})

    @override_settings(COMMUNITY_MODERATION_MAX_ATTEMPTS=1)
    def test_failure_and_retry_keep_public_current_revision(self):
        _, post = self.submit()
        job = post.moderation_jobs.get()
        job.payload['mode'] = 'live'
        job.save(update_fields=['payload'])
        run_one(lambda payload: (_ for _ in ()).throw(TimeoutError()))
        self.assert_public(post)
        retried = retry_job(job.pk, self.staff)
        self.assertEqual(retried.revision, job.revision)
        self.assert_public(post)

    def test_routine_cannot_revoke_confirmed_sanction_or_admin_hide(self):
        _, post = self.submit()
        run_one(lambda payload: ([finding('privacy', '', '검토')], {}))
        job = post.moderation_jobs.get()
        review_job(job.pk, self.staff, job.revision, job.review_revision, [job.findings[0]['id']], 'confirm', '확인')
        with transaction.atomic():
            queued = enqueue_locked(CommunityPost.objects.select_for_update().get(pk=post.pk), enforce_limit=False, trigger='routine')
        self.assertEqual(queued.revision, job.revision)
        run_one(lambda payload: ([], {}))
        self.assertEqual(self.client.get(f'/api/v1/community/posts/{post.pk}/').status_code, 404)
        job.refresh_from_db()
        review_job(job.pk, self.staff, job.revision, job.review_revision, [job.findings[0]['id']], 'dismiss', '복구 확인')
        self.assert_public(post)
        CommunityPost.objects.filter(pk=post.pk).update(is_hidden=True)
        self.client.patch(f'/api/v1/community/posts/{post.pk}/', {'content': '수정'}, format='json')
        self.assertEqual(self.client.get(f'/api/v1/community/posts/{post.pk}/').status_code, 404)
