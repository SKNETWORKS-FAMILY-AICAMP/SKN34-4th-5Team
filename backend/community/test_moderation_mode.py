import os
from types import SimpleNamespace as NS
from unittest.mock import MagicMock, patch
from django.contrib.auth import get_user_model
from django.test import override_settings
from rest_framework.test import APITestCase
from .models import CommunityPost, ModerationJob, ModerationSettings
from .moderation_worker import run_one


@override_settings(COMMUNITY_MODERATION_MODE='manual', COMMUNITY_POLICY_VERSION='community-v1')
class ModerationModeTests(APITestCase):
    url = '/api/v1/community/admin/moderation/settings/'

    def setUp(self):
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
        self.assertFalse(ModerationSettings.objects.exists())

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
        ModerationSettings.objects.create(mode='openai')
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
        self.assertEqual(post.moderation_status, 'error')
        self.assertIsNone(post.first_published_at)

    def test_missing_provider_result_is_not_approved(self):
        from .moderation_checks import check
        client = MagicMock()
        client.moderations.create.return_value.results = []
        with patch('openai.OpenAI', return_value=client), self.assertRaises(RuntimeError):
            check(dict(text='야구', image_ids=[], mode='openai', policy_version='community-v1'))

    @override_settings(COMMUNITY_MODERATION_MODE='openai')
    def test_automatic_default_and_explicit_manual_override(self):
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
        ModerationSettings.objects.create(mode='openai')
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
