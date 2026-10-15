"""Frozen A/B observations: run explicitly against HEAD and candidate test DBs.

This only records observations; DeepEval/Ragas score the same expected privacy contract.
"""
import json
from django.contrib.auth import get_user_model
from rest_framework.test import APITestCase
from accounts.models import PointTransaction


class PublicationProbe(APITestCase):
    def test_observe_unreviewed_submission(self):
        owner = get_user_model().objects.create_user(username='ab-publication-probe')
        self.client.force_authenticate(owner)
        result = self.client.post('/api/v1/community/posts/', dict(board='free', teamCode='', category='잡담', title='A/B privacy probe', content='같은 입력으로 비공개 대기를 확인합니다.'), format='json', HTTP_IDEMPOTENCY_KEY='ab-probe')
        self.assertIn(result.status_code, (201, 202), result.data)
        post_id = result.data['id']
        self.client.force_authenticate(None)
        listed = self.client.get('/api/v1/community/posts/').data
        rows = [
            {'id': 'list', 'actual': 'private' if post_id not in [p['id'] for p in listed] else 'public', 'expected': 'private'},
            {'id': 'detail', 'actual': 'private' if self.client.get(f'/api/v1/community/posts/{post_id}/').status_code == 404 else 'public', 'expected': 'private'},
            {'id': 'comments', 'actual': 'private' if self.client.get(f'/api/v1/community/posts/{post_id}/comments/').status_code == 404 else 'public', 'expected': 'private'},
            {'id': 'points', 'actual': 'deferred' if not PointTransaction.objects.filter(user=owner, source_type='post').exists() else 'granted', 'expected': 'deferred'},
        ]
        print('MODERATION_PROBE=' + json.dumps(rows))
