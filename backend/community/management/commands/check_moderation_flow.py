"""Local HTTP smoke test with disposable users and posts; no model calls."""
import json
import time
import uuid
from urllib.request import Request, urlopen
from urllib.error import HTTPError

from community.moderation_config import current_mode
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from rest_framework_simplejwt.tokens import AccessToken
from community.models import CommunityPost, ModerationJob
from accounts.models import PointTransaction


class Command(BaseCommand):
    help = 'Exercise the local HTTP API and running manual worker with disposable records.'

    def handle(self, *args, **options):
        if current_mode() != 'manual':
            raise CommandError('Smoke test requires manual mode; no provider calls are authorized by this command.')
        users, post_id = [], None
        key = f'moderation-smoke-{uuid.uuid4().hex}'

        def request(path, token=None, body=None, method=None):
            headers = {'Content-Type': 'application/json', 'Idempotency-Key': key}
            if token:
                headers['Authorization'] = f'Bearer {token}'
            req = Request('http://127.0.0.1:8000/api/v1/community/' + path,
                          data=json.dumps(body).encode() if body is not None else None,
                          headers=headers, method=method)
            try:
                response = urlopen(req, timeout=10)
            except HTTPError as error:
                response = error
            with response:
                raw = response.read()
                return response.status, json.loads(raw) if raw else None

        try:
            for suffix, staff in (('author', False), ('staff', True)):
                user = get_user_model().objects.create_user(username=f'{key[-16:]}-{suffix}', is_staff=staff)
                users.append(user)
            author, staff = (str(AccessToken.for_user(user)) for user in users)
            code, result = request('posts/', author, {'board': 'free', 'teamCode': '', 'category': '잡담', 'title': '[로컬 자동 점검] 야구 응원', 'content': 'wtf hanwha Eagles is at another level', 'isTrade': False, 'isAdvertisement': False})
            if code != 202:
                raise CommandError(f'Creation returned {code}')
            post_id = result['id']
            if request(f'posts/{post_id}/')[0] != 404:
                raise CommandError('Pending post was publicly visible')
            job = None
            for _ in range(30):
                job = ModerationJob.objects.get(post_id=post_id, revision=1)
                if job.state == 'done':
                    break
                time.sleep(0.5)
            if job is None or job.state != 'done':
                raise CommandError('Running worker did not finish within 15 seconds')
            review = dict(revision=1, review_revision=0, finding_ids=[item['id'] for item in job.findings], action='dismiss', note='자동 점검용 무해한 야구 응원 글 확인')
            if request(f'admin/moderation/{job.pk}/review/', staff, review)[0] != 200:
                raise CommandError('Staff review failed')
            if request(f'posts/{post_id}/')[0] != 200:
                raise CommandError('Approved post was not visible')
            if request(f'posts/{post_id}/', author, {'title': '[로컬 자동 점검] 수정 후 재검사'}, method='PATCH')[0] != 202:
                raise CommandError('Edit did not requeue')
            if request(f'posts/{post_id}/')[0] != 404:
                raise CommandError('Edited post was publicly visible')
            if request(f'admin/moderation/{job.pk}/review/', staff, review)[0] != 409:
                raise CommandError('Old revision review was accepted')
            self.stdout.write(self.style.SUCCESS('HTTP + running worker: pending 404 -> reviewed 200 -> edited 404; stale review 409.'))
        finally:
            if post_id:
                CommunityPost.objects.filter(pk=post_id, owner__in=users).delete()
            for user in users:
                # These are only the accounts created in this command, never real members.
                PointTransaction.objects.filter(user=user).delete()
                user.delete()
            self.stdout.write('Disposable smoke-test records removed.')
