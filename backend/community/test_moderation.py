from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.management import call_command, get_commands
from django.test import TransactionTestCase, override_settings
from django.utils import timezone
from rest_framework.test import APITestCase

from .models import CommunityPost, CommunityComment, ModerationJob, ModerationReview, ModerationSettings
from .moderation_checks import check, finding
from .moderation_worker import claim, finish, run_one


@override_settings(COMMUNITY_POLICY_VERSION='community-v1')
class ModerationIntegrationTests(APITestCase):
    def test_unchecked_command_backfills_legacy_content_without_rechecking(self):
        from io import StringIO
        from services.community_moderation import enqueue_unchecked
        completed = self.submit(key='completed')
        run_one()
        failed = self.submit(key='failed')
        job = failed.moderation_jobs.get()
        job.payload['mode'] = 'live'
        job.save(update_fields=['payload'])
        with override_settings(COMMUNITY_MODERATION_MAX_ATTEMPTS=1):
            run_one(lambda payload: (_ for _ in ()).throw(TimeoutError()))
        legacy = CommunityPost.objects.create(owner=self.owner, board='free',
            title='과거 글', content='야구 이야기', category='잡담',
            moderation_status='approved', approved_revision=0, is_hidden=True)
        comment = CommunityComment.objects.create(post=legacy, author=self.owner,
            content='과거 댓글', moderation_status='approved', approved_revision=0)
        counts = enqueue_unchecked()
        self.assertGreaterEqual(counts['posts'], 1)
        self.assertGreaterEqual(counts['comments'], 1)
        legacy.refresh_from_db()
        self.assertEqual(legacy.moderation_status, 'pending')
        self.assertTrue(legacy.is_hidden)
        self.assertEqual(enqueue_unchecked(), {'posts': 0, 'comments': 0})
        with patch('llm.commands.run_moderation_worker.close_old_connections'):
            call_command('filter_unchecked_content', stdout=StringIO())
        legacy.refresh_from_db()
        comment.refresh_from_db()
        self.assertEqual((legacy.moderation_status, comment.moderation_status), ('review', 'review'))
        self.assertTrue(legacy.is_hidden)
        self.assertEqual(completed.moderation_jobs.count(), 1)
        self.assertEqual(failed.moderation_jobs.get().state, 'failed')
        self.assertEqual(enqueue_unchecked(), {'posts': 0, 'comments': 0})

    def test_llm_command_drains_post_and_comment_through_service(self):
        parent = self.submit(key='parent')
        run_one(lambda payload: ([], {}))
        response = self.client.post(f'/api/v1/community/posts/{parent.pk}/comments/',
                                    {'content': '댓글 검사 대상'}, format='json')
        self.assertEqual(response.status_code, 202)
        post = self.submit(key='pending-post', content='게시글 검사 대상')
        self.assertEqual(get_commands()['run_moderation_worker'], 'llm')
        # APITestCase owns an outer transaction; production connection cleanup
        # must not close that test-owned connection between worker iterations.
        with patch('services.community_moderation_policy.check', wraps=check) as provider, \
                patch('llm.commands.run_moderation_worker.close_old_connections'):
            call_command('run_moderation_worker', once=True)
        self.assertEqual(provider.call_count, 2)
        self.assertEqual({call.args[0]['text'] for call in provider.call_args_list},
                         {'댓글 검사 대상', '한화 경기\n게시글 검사 대상'})
        post.refresh_from_db()
        self.assertEqual(post.moderation_status, 'review')
        self.assertEqual(parent.comments.get().moderation_status, 'review')
        self.assertFalse(ModerationJob.objects.filter(state__in=('queued', 'running')).exists())
        self.assertEqual(self.client.get(f'/api/v1/community/posts/{post.pk}/').status_code, 404)
        self.assertEqual(self.client.get(f'/api/v1/community/posts/{parent.pk}/comments/').data, [])

    def setUp(self):
        ModerationSettings.objects.create(mode='manual')
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

    def test_legacy_live_provider_failure_stays_private_and_retries(self):
        post = self.submit()
        job = post.moderation_jobs.get()
        job.payload['mode'] = 'live'
        job.save(update_fields=['payload'])
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

    @override_settings(COMMUNITY_MODERATION_WRITES_PER_HOUR=1)
    def test_deleted_post_does_not_reset_account_limit(self):
        post = self.submit()
        self.assertEqual(self.client.delete(f'/api/v1/community/posts/{post.pk}/').status_code, 204)
        self.assertFalse(ModerationJob.objects.filter(post=post.pk).exists())
        data = dict(board='free', teamCode='', category='잡담', title='둘째', content='본문')
        self.assertEqual(self.client.post('/api/v1/community/posts/', data, format='json', HTTP_IDEMPOTENCY_KEY='second').status_code, 429)
        self.client.force_authenticate(self.other)
        self.assertEqual(self.client.post('/api/v1/community/posts/', data, format='json', HTTP_IDEMPOTENCY_KEY='second').status_code, 202)

    @override_settings(COMMUNITY_MODERATION_WRITES_PER_HOUR=1)
    def test_deleted_comment_does_not_reset_account_limit(self):
        post = CommunityPost.objects.create(owner=self.other, board='free', category='잡담', title='공개', content='본문', moderation_status='approved', approved_revision=0)
        url = f'/api/v1/community/posts/{post.pk}/comments/'
        response = self.client.post(url, {'content': '댓글'}, format='json')
        self.assertEqual(response.status_code, 202)
        self.assertEqual(self.client.delete(f"/api/v1/community/comments/{response.data['id']}/").status_code, 204)
        self.assertEqual(self.client.post(url, {'content': '둘째'}, format='json').status_code, 429)
        self.client.force_authenticate(self.other)
        self.assertEqual(self.client.post(url, {'content': '다른 계정'}, format='json').status_code, 202)

    @override_settings(COMMUNITY_MODERATION_WRITES_PER_HOUR=1)
    def test_expired_admission_is_pruned_without_retaining_deleted_content(self):
        from .models import ModerationAdmission
        post = self.submit()
        self.client.delete(f'/api/v1/community/posts/{post.pk}/')
        admission = ModerationAdmission.objects.get(account=self.owner)
        self.assertEqual({field.name for field in admission._meta.fields}, {'id', 'account', 'created_at'})
        ModerationAdmission.objects.filter(pk=admission.pk).update(created_at=timezone.now() - timedelta(hours=2))
        self.submit(key='after-expiry')
        self.assertEqual(ModerationAdmission.objects.filter(account=self.owner).count(), 1)
        self.assertFalse(ModerationAdmission.objects.filter(pk=admission.pk).exists())

    def test_admission_migration_preserves_recent_jobs_and_reviews(self):
        from importlib import import_module
        from django.apps import apps
        from django.db import connection
        from .models import ModerationAdmission

        post = self.submit()
        run_one()
        job = post.moderation_jobs.get()
        self.review(job)
        before = ModerationJob.objects.get(pk=job.pk)
        review_id = job.reviews.get().pk
        comment = CommunityComment.objects.create(post=post, author=self.other, content='댓글')
        comment_job = comment.moderation_jobs.create(revision=0, payload={'text': '댓글'})
        old_job = post.moderation_jobs.create(revision=99, payload={'text': '과거'})
        ModerationJob.objects.filter(pk=old_job.pk).update(created_at=timezone.now() - timedelta(hours=2))
        ModerationAdmission.objects.all().delete()
        migration = import_module('community.migrations.0012_moderation_admission')
        with connection.schema_editor() as schema_editor:
            migration.seed_recent_admissions(apps, schema_editor)
        self.assertCountEqual(ModerationAdmission.objects.values_list('account_id', flat=True), [self.owner.pk, self.other.pk])
        self.assertEqual(ModerationAdmission.objects.get(account=self.other).created_at, comment_job.created_at)
        job.refresh_from_db()
        self.assertEqual((job.payload, job.findings, job.state), (before.payload, before.findings, before.state))
        self.assertTrue(ModerationReview.objects.filter(pk=review_id).exists())

    def test_boolean_submission_retries_use_validated_normalization(self):
        for index, value in enumerate(('true', 'TRUE', 'false', 0, 1)):
            with self.subTest(value=value):
                data = dict(board='free', teamCode='', category='잡담', title='한화 경기', content='본문', isTrade=value, isAdvertisement=value)
                key = f'boolean-{index}'
                first = self.client.post('/api/v1/community/posts/', data, format='json', HTTP_IDEMPOTENCY_KEY=key)
                self.assertEqual(first.status_code, 202, first.data)
                self.client.patch(f"/api/v1/community/posts/{first.data['id']}/", {'content': '나중 수정'}, format='json')
                retry = self.client.post('/api/v1/community/posts/', data, format='json', HTTP_IDEMPOTENCY_KEY=key)
                self.assertEqual(retry.status_code, 200, retry.data)
                changed = self.client.post('/api/v1/community/posts/', {**data, 'content': '다른 본문'}, format='json', HTTP_IDEMPOTENCY_KEY=key)
                self.assertEqual(changed.status_code, 409)

    def test_chatbot_search_does_not_leak_pending_content(self):
        post = self.submit()
        from llm.tools.community import create_community_tools
        result = create_community_tools()[0].invoke({'query': 'at another level'})
        self.assertNotIn(post.pk, str(result))
        run_one(lambda payload: ([], {}))
        result = create_community_tools()[0].invoke({'query': 'at another level'})
        self.assertIn(post.pk, str(result))


@override_settings(COMMUNITY_MODERATION_WRITES_PER_HOUR=1, COMMUNITY_POLICY_VERSION='community-v1')
class ModerationAdmissionConcurrencyTests(TransactionTestCase):
    def test_concurrent_account_admission_commits_only_one(self):
        from concurrent.futures import ThreadPoolExecutor
        from threading import Barrier
        from django.db import close_old_connections, connection, transaction
        from rest_framework.exceptions import Throttled
        from .models import ModerationAdmission
        from .moderation_service import enqueue_locked

        if connection.vendor != 'postgresql':
            self.skipTest('PostgreSQL row locks required')
        owner = get_user_model().objects.create_user(username='concurrent-admission')
        barrier = Barrier(2)

        def submit(index):
            close_old_connections()
            try:
                with transaction.atomic():
                    with connection.cursor() as cursor:
                        cursor.execute("SET LOCAL lock_timeout = '10s'")
                    post = CommunityPost.objects.create(owner_id=owner.pk, post_number=f'{900000 + index:06}', board='free', category='잡담', title='경합', content='본문')
                    barrier.wait(timeout=10)
                    enqueue_locked(post)
                return 'accepted'
            except Throttled:
                return 'throttled'
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(submit, (1, 2)))
        self.assertCountEqual(results, ['accepted', 'throttled'])
        self.assertEqual(CommunityPost.objects.filter(owner=owner).count(), 1)
        self.assertEqual(ModerationAdmission.objects.filter(account=owner).count(), 1)
        self.assertEqual(ModerationJob.objects.filter(post__owner=owner).count(), 1)
