import os
from types import SimpleNamespace as NS
from unittest.mock import MagicMock, patch
from django.contrib.auth import get_user_model
from django.test import override_settings
from rest_framework.test import APITestCase
from .models import CommunityPost, ModerationJob, ModerationSettings
from .moderation_worker import run_one


@override_settings(COMMUNITY_POLICY_VERSION='community-v1')
class ModerationModeTests(APITestCase):
    url = '/api/v1/community/admin/moderation/settings/'

    def setUp(self):
        ModerationSettings.objects.create(mode='manual')
        self.staff = get_user_model().objects.create_user(username='mode-admin', is_staff=True)
        self.member = get_user_model().objects.create_user(username='mode-member')

    def submit(self, key):
        self.client.force_authenticate(self.member)
        response = self.client.post('/api/v1/community/posts/', {
            'board': 'free', 'teamCode': '', 'category': '잡담', 'title': '한화 경기', 'content': '야구 이야기',
        }, format='json', HTTP_IDEMPOTENCY_KEY=key)
        self.assertEqual(response.status_code, 202, response.data)
        return CommunityPost.objects.get(pk=response.data['id'])

    def test_permissions_and_validation(self):
        for user in (None, self.member):
            self.client.force_authenticate(user)
            self.assertIn(self.client.get(self.url).status_code, (401, 403))
            self.assertIn(self.client.post(self.url, {'mode': 'openai'}).status_code, (401, 403))
        self.client.force_authenticate(self.staff)
        self.assertEqual(self.client.get(self.url).data['mode'], 'manual')
        self.assertEqual(self.client.post(self.url, {'mode': 'live'}).status_code, 400)
        with patch.dict(os.environ, {'OPENAI_API_KEY': ''}):
            self.assertFalse(self.client.get(self.url).data['automaticAvailable'])
            self.assertEqual(self.client.post(self.url, {'mode': 'openai'}).status_code, 400)
        self.assertEqual(ModerationSettings.objects.get().mode, 'manual')

    @patch.dict(os.environ, {'OPENAI_API_KEY': 'synthetic-test-only'})
    def test_switch_persists_and_only_changes_new_snapshots(self):
        old = self.submit('old')
        self.client.force_authenticate(self.staff)
        response = self.client.post(self.url, {'mode': 'openai'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(ModerationSettings.objects.get().updated_by, self.staff)
        self.assertIn('no-store', response['Cache-Control'])
        self.assertNotIn('synthetic-test-only', str(response.data))
        new = self.submit('new')
        self.assertEqual(old.moderation_jobs.get().payload['mode'], 'manual')
        self.assertEqual(new.moderation_jobs.get().payload['mode'], 'openai')
        self.client.force_authenticate(self.staff)
        self.assertEqual(self.client.post(self.url, {'mode': 'manual'}).status_code, 200)
        self.assertEqual(ModerationSettings.objects.count(), 1)
        self.assertEqual(new.moderation_jobs.get().payload['mode'], 'openai')

    def test_native_flags_and_error_keep_correct_visibility(self):
        ModerationSettings.objects.update(mode='openai')
        for index, flagged in enumerate((False, True)):
            post = self.submit(str(index))
            categories, scores, client = MagicMock(), MagicMock(), MagicMock()
            categories.model_dump.return_value = {'harassment': flagged}
            scores.model_dump.return_value = {'harassment': float(flagged)}
            client.moderations.create.return_value = NS(model='synthetic', results=[NS(flagged=flagged, categories=categories, category_scores=scores)])
            with patch('openai.OpenAI', return_value=client):
                run_one()
            client.moderations.create.assert_called_once_with(model='omni-moderation-latest', input='한화 경기\n야구 이야기')
            client.embeddings.create.assert_not_called()
            client.responses.parse.assert_not_called()
            post.refresh_from_db()
            self.assertEqual(post.moderation_status, 'review' if flagged else 'approved')
            self.assertEqual(post.first_published_at is None, flagged)
        post = self.submit('error')
        with patch('openai.OpenAI', side_effect=TimeoutError()), override_settings(COMMUNITY_MODERATION_MAX_ATTEMPTS=1):
            run_one()
        post.refresh_from_db()
        self.assertEqual(post.moderation_status, 'review')
        self.assertIsNotNone(post.first_published_at)
        self.assertEqual(post.approved_revision, post.content_revision)

    def test_missing_provider_result_is_not_approved(self):
        from .moderation_checks import check
        client = MagicMock()
        client.moderations.create.return_value.results = []
        with patch('openai.OpenAI', return_value=client), self.assertRaises(RuntimeError):
            check(dict(text='야구', image_ids=[], mode='openai', policy_version='community-v1'))

    @override_settings(COMMUNITY_MODERATION_MODE='manual')
    @patch.dict(os.environ, {'COMMUNITY_MODERATION_MODE': 'manual'})
    def test_automatic_default_and_explicit_manual_override(self):
        ModerationSettings.objects.all().delete()
        self.assertFalse(ModerationSettings.objects.exists())
        post = self.submit('automatic-default')
        self.assertEqual(post.moderation_jobs.get().payload['mode'], 'openai')
        ModerationSettings.objects.create(mode='manual')
        manual = self.submit('manual-override')
        self.assertEqual(manual.moderation_jobs.get().payload['mode'], 'manual')

    def test_requeue_manual_preserves_history_and_hidden_state(self):
        from django.db import transaction
        from .moderation_service import enqueue_locked, locked_target
        post = self.submit('manual-history')
        run_one()
        old = post.moderation_jobs.get()
        self.client.force_authenticate(self.staff)
        response = self.client.post(f'/api/v1/community/admin/moderation/{old.pk}/review/', {
            'revision': old.revision, 'review_revision': old.review_revision,
            'finding_ids': [old.findings[0]['id']], 'action': 'dismiss', 'note': '원문 확인',
        }, format='json')
        self.assertEqual(response.status_code, 200)
        post.refresh_from_db()
        published_at = post.first_published_at
        post.is_hidden = True
        post.save(update_fields=['is_hidden'])
        ModerationSettings.objects.update(mode='openai')
        with transaction.atomic():
            new = enqueue_locked(locked_target(old), enforce_limit=False)
        self.assertEqual(new.payload['mode'], 'openai')
        self.assertEqual(new.revision, old.revision + 1)
        self.assertEqual(old.reviews.count(), 1)
        run_one(lambda payload: ([], {}))
        post.refresh_from_db()
        self.assertTrue(post.is_hidden)
        self.assertEqual(post.first_published_at, published_at)
        self.assertEqual(post.moderation_jobs.count(), 2)

    @patch.dict(os.environ, {'OPENAI_API_KEY': ''})
    def test_default_missing_key_is_public_and_immediately_reviewable(self):
        ModerationSettings.objects.all().delete()
        post = self.submit('missing-key')
        with patch('socket.socket.connect', side_effect=AssertionError('network forbidden')):
            run_one()
        post.refresh_from_db()
        job = post.moderation_jobs.get()
        self.assertEqual((job.state, job.attempts, post.moderation_status), ('done', 1, 'review'))
        self.assertEqual(job.payload['mode'], 'openai')
        self.assertEqual(job.error_code, 'automatic_check_failed')
        self.assertEqual(job.evidence['fallback'], 'manual_review')
        self.assertEqual([f['code'] for f in job.findings], ['manual_review'])
        self.assertIsNotNone(post.first_published_at)
        self.assertEqual(post.approved_revision, post.content_revision)
        self.assertFalse(ModerationSettings.objects.exists())
        self.client.force_authenticate(None)
        self.assertEqual(self.client.get(f'/api/v1/community/posts/{post.pk}/').status_code, 200)
        self.client.force_authenticate(self.member)
        self.assertEqual(self.client.post(f'/api/v1/community/admin/moderation/{job.pk}/review/', {}).status_code, 403)
        self.client.force_authenticate(self.staff)
        self.assertEqual(self.client.post(f'/api/v1/community/admin/moderation/{job.pk}/review/', {
            'revision': job.revision, 'review_revision': job.review_revision,
            'finding_ids': [job.findings[0]['id']], 'action': 'dismiss', 'note': '원문 확인',
        }, format='json').status_code, 200)
        post.refresh_from_db()
        self.assertEqual(post.moderation_status, 'approved')
        self.assertIsNotNone(post.first_published_at)

    def test_timeout_and_malformed_native_results_are_public_review_not_retries(self):
        ModerationSettings.objects.update(mode='openai')
        for index, malformed in enumerate((None, [], [NS(flagged='false')])):
            with self.subTest(index=index):
                post = self.submit(f'failure-{index}')
                client = MagicMock()
                if malformed is None:
                    client.moderations.create.side_effect = TimeoutError('synthetic secret must not persist')
                else:
                    client.moderations.create.return_value.results = malformed
                with patch('openai.OpenAI', return_value=client):
                    run_one()
                post.refresh_from_db()
                job = post.moderation_jobs.get()
                self.assertEqual((job.state, job.attempts, post.moderation_status), ('done', 1, 'review'))
                self.assertIsNotNone(post.first_published_at)
                self.assertEqual(post.approved_revision, post.content_revision)
                self.assertNotIn('synthetic secret', str([job.findings, job.evidence, job.error_code]))
                self.assertEqual(ModerationSettings.objects.get().mode, 'openai')
                self.assertFalse(run_one())
                self.assertEqual(self.client.get(f'/api/v1/community/posts/{post.pk}/').status_code, 200)

    def test_fallback_preserves_image_review_and_manual_hide(self):
        from .moderation_worker import claim, finish
        ModerationSettings.objects.update(mode='openai')
        post = self.submit('image-failure')
        post.is_hidden = True
        post.save(update_fields=['is_hidden'])
        job = post.moderation_jobs.get()
        job.payload['image_ids'] = ['synthetic-image']
        job.save(update_fields=['payload'])
        ref = claim()
        finish(ref, error=TimeoutError())
        job.refresh_from_db()
        post.refresh_from_db()
        self.assertEqual({f['code'] for f in job.findings}, {'manual_review', 'image_review'})
        self.assertTrue(post.is_hidden)
        self.assertIsNone(post.first_published_at)
        self.client.force_authenticate(self.staff)
        self.assertEqual(self.client.post(f'/api/v1/community/admin/moderation/{job.pk}/review/', {
            'revision': job.revision, 'review_revision': job.review_revision,
            'finding_ids': [f['id'] for f in job.findings if f['code'] == 'manual_review'],
            'action': 'dismiss', 'note': '본문만 확인',
        }, format='json').status_code, 200)
        post.refresh_from_db()
        self.assertEqual(post.moderation_status, 'review')
        self.assertIsNone(post.first_published_at)

    def fail_check(self):
        run_one(lambda payload: (_ for _ in ()).throw(TimeoutError('synthetic')))

    def review(self, job, action='dismiss', codes=None):
        job.refresh_from_db()
        self.client.force_authenticate(self.staff)
        response = self.client.post(f'/api/v1/community/admin/moderation/{job.pk}/review/', {
            'revision': job.revision, 'review_revision': job.review_revision,
            'finding_ids': [f['id'] for f in job.findings if codes is None or f['code'] in codes],
            'action': action, 'note': '원문 확인',
        }, format='json')
        self.assertEqual(response.status_code, 200, response.data)

    def test_public_fallback_is_listed_and_confirm_dismiss_preserve_one_publication(self):
        ModerationSettings.objects.update(mode='openai')
        post = self.submit('public-review')
        with patch('community.moderation_service.grant_points') as grant:
            self.fail_check()
            post.refresh_from_db()
            published = post.first_published_at
            job = post.moderation_jobs.get()
            self.client.force_authenticate(self.staff)
            listing = self.client.get('/api/v1/community/admin/moderation/')
            self.assertIn(str(job.pk), [str(row['id']) for row in listing.data['results']])
            self.assertEqual(job.findings[0]['resolution'], 'open')
            self.assertNotIn('flagged', job.evidence)
            self.review(job, 'confirm')
            post.refresh_from_db()
            self.assertEqual(post.moderation_status, 'rejected')
            self.assertIsNone(post.approved_revision)
            self.assertEqual(self.client.get(f'/api/v1/community/posts/{post.pk}/').status_code, 404)
            self.review(job)
            self.review(job)
            post.refresh_from_db()
            self.assertEqual(post.first_published_at, published)
            self.assertEqual(grant.call_count, 1)
            self.assertEqual(job.reviews.count(), 3)
            self.assertEqual(self.client.get(f'/api/v1/community/posts/{post.pk}/').status_code, 200)

    def test_fallback_comments_counts_parent_hold_and_deferred_points(self):
        from .models import CommunityComment
        from .moderation_service import enqueue_locked
        from django.db import transaction
        ModerationSettings.objects.update(mode='openai')
        post = self.submit('comment-parent')
        run_one(lambda payload: ([], {}))
        self.client.force_authenticate(self.member)
        response = self.client.post(f'/api/v1/community/posts/{post.pk}/comments/', {'content': '댓글'}, format='json')
        self.assertEqual(response.status_code, 202)
        comment = CommunityComment.objects.get(pk=response.data['id'])
        with patch('community.moderation_service.grant_points') as grant:
            self.fail_check()
            comment.refresh_from_db()
            job = comment.moderation_jobs.get()
            self.assertEqual(comment.moderation_status, 'review')
            self.assertIsNotNone(comment.first_published_at)
            self.assertEqual(self.client.get(f'/api/v1/community/posts/{post.pk}/').data['commentCount'], 1)
            self.assertEqual(len(self.client.get(f'/api/v1/community/posts/{post.pk}/comments/').data), 1)
            self.review(job, 'confirm')
            self.assertEqual(self.client.get(f'/api/v1/community/posts/{post.pk}/').data['commentCount'], 0)
            self.review(job)
            self.assertEqual(grant.call_count, 1)
        # A privately revised parent must hold both access and first-publication points.
        with transaction.atomic():
            enqueue_locked(CommunityPost.objects.select_for_update().get(pk=post.pk), enforce_limit=False)
        self.fail_check()  # parent fallback restores publication
        CommunityPost.objects.filter(pk=post.pk).update(approved_revision=None, moderation_status='review')
        held = CommunityComment.objects.create(post=post, author=self.member, content='대기 댓글')
        with transaction.atomic():
            enqueue_locked(held, enforce_limit=False)
        with patch('community.moderation_service.grant_points') as grant:
            self.fail_check()
            held.refresh_from_db()
            self.assertEqual(held.approved_revision, held.content_revision)
            self.assertIsNone(held.first_published_at)
            self.assertEqual(grant.call_count, 0)
            self.assertEqual(self.client.get(f'/api/v1/community/posts/{post.pk}/comments/').status_code, 404)
            self.review(post.moderation_jobs.order_by('-revision').first())
            held.refresh_from_db()
            self.assertIsNotNone(held.first_published_at)
            self.assertEqual(grant.call_count, 1)
            self.assertEqual(self.client.get(f'/api/v1/community/posts/{post.pk}/').data['commentCount'], 2)

    def test_hidden_text_fallback_never_unhides_or_grants_points(self):
        ModerationSettings.objects.update(mode='openai')
        post = self.submit('hidden-text')
        CommunityPost.objects.filter(pk=post.pk).update(is_hidden=True)
        with patch('community.moderation_service.grant_points') as grant:
            self.fail_check()
            self.review(post.moderation_jobs.get())
            grant.assert_not_called()
        post.refresh_from_db()
        self.assertTrue(post.is_hidden)
        self.assertIsNone(post.first_published_at)
        self.assertEqual(self.client.get(f'/api/v1/community/posts/{post.pk}/').status_code, 404)

    def test_image_reason_blocks_until_dismissed_but_manual_fallback_stays_open(self):
        ModerationSettings.objects.update(mode='openai')
        post = self.submit('image-boundary')
        job = post.moderation_jobs.get()
        job.payload['image_ids'] = ['synthetic']
        job.save(update_fields=['payload'])
        self.fail_check()
        post.refresh_from_db()
        self.assertIsNone(post.approved_revision)
        self.review(job, 'confirm', {'image_review'})
        post.refresh_from_db()
        self.assertEqual(post.moderation_status, 'rejected')
        self.review(job, codes={'image_review'})
        post.refresh_from_db()
        job.refresh_from_db()
        self.assertEqual(post.moderation_status, 'review')
        self.assertEqual(post.approved_revision, post.content_revision)
        self.assertEqual(next(f for f in job.findings if f['code'] == 'manual_review')['resolution'], 'open')
        self.review(job, 'confirm', {'manual_review'})
        post.refresh_from_db()
        self.assertIsNone(post.approved_revision)

    def test_stale_failure_cannot_override_new_lease_or_revision(self):
        from datetime import timedelta
        from django.utils import timezone
        from .moderation_worker import claim, finish
        ModerationSettings.objects.update(mode='openai')
        post = self.submit('stale-failure')
        stale = claim()
        ModerationJob.objects.filter(pk=stale.pk).update(lease_until=timezone.now() - timedelta(seconds=1))
        replacement = claim()
        self.assertFalse(finish(stale, error=TimeoutError()))
        CommunityPost.objects.filter(pk=post.pk).update(content_revision=2)
        self.assertTrue(finish(replacement, error=TimeoutError()))
        post.refresh_from_db()
        job = post.moderation_jobs.get()
        self.assertEqual((post.moderation_status, job.state, job.findings), ('pending', 'cancelled', []))

    def test_storage_failures_are_not_misclassified_as_provider_failures(self):
        from django.db import DatabaseError
        ModerationSettings.objects.update(mode='openai')
        self.submit('storage-failure')
        with patch('services.community_moderation.finish', side_effect=DatabaseError()) as finish:
            with self.assertRaises(DatabaseError):
                run_one(lambda payload: ([], {}))
            self.assertEqual(finish.call_count, 1)
        # The checker boundary also propagates DB errors without generating a review finding.
        ModerationJob.objects.update(state='queued')
        with patch('services.community_moderation.finish') as finish:
            with self.assertRaises(DatabaseError):
                run_one(lambda payload: (_ for _ in ()).throw(DatabaseError()))
            finish.assert_not_called()
