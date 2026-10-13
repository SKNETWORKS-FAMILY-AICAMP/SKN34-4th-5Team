"""Transactional publication boundary; external providers are never called here."""
import hashlib
import json
from datetime import timedelta

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import APIException, PermissionDenied, ValidationError, Throttled

from accounts.point_policy import POST, COMMENT, grant_definition
from accounts.point_service import grant_points
from .models import CommunityPost, CommunityComment, ModerationAdmission, ModerationJob, ModerationReview
from .visibility import approved, post_public, public_comments


class ModerationConflict(APIException):
    status_code = 409
    default_detail = '내용 또는 검토 상태가 변경되었습니다. 다시 확인해 주세요.'


def submission_hash(values):
    fields = ('board', 'team_code', 'category', 'title', 'content', 'content_doc', 'is_trade', 'is_advertisement')
    data = {k: values.get(k, False if k.startswith('is_') else None) for k in fields}
    return hashlib.sha256(json.dumps(data, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def locked_target(job):
    if job.post_id:
        return CommunityPost.objects.select_for_update().filter(pk=job.post_id).first()
    parent_id = CommunityComment.objects.filter(pk=job.comment_id).values_list('post_id', flat=True).first()
    parent = CommunityPost.objects.select_for_update().filter(pk=parent_id).first()
    if parent is None:
        return None
    comment = CommunityComment.objects.select_for_update().filter(pk=job.comment_id, post=parent).first()
    if comment:
        comment.post = parent
    return comment


def enqueue_locked(obj, *, enforce_limit=True):
    """Caller must hold the content lock (or own its creation transaction)."""
    is_post = isinstance(obj, CommunityPost)
    owner_id = obj.owner_id if is_post else obj.author_id
    # Serialize per-account write admission; never trust a client-provided counter.
    if enforce_limit and owner_id:
        get_user_model().objects.select_for_update(no_key=True).get(pk=owner_id)
        since = timezone.now() - timedelta(hours=1)
        admissions = ModerationAdmission.objects.filter(account_id=owner_id)
        admissions.filter(created_at__lt=since).delete()
        if admissions.count() >= settings.COMMUNITY_MODERATION_WRITES_PER_HOUR:
            raise Throttled(detail='게시 요청이 많습니다. 잠시 후 다시 시도해 주세요.')
        ModerationAdmission.objects.create(account_id=owner_id)
    related = obj.moderation_jobs
    related.filter(state__in=('queued', 'running')).update(state='cancelled', lease_token=None, lease_until=None)
    obj.content_revision += 1
    obj.approved_revision = None
    obj.moderation_status = 'pending'
    obj.save(update_fields=('content_revision', 'approved_revision', 'moderation_status'))
    payload = {
        'text': f'{obj.title}\n{obj.content}' if is_post else obj.content,
        'is_trade': obj.is_trade if is_post else False,
        'is_advertisement': obj.is_advertisement if is_post else False,
        'image_ids': [str(i) for i in obj.images.values_list('id', flat=True)] if is_post else [],
        'policy_version': settings.COMMUNITY_POLICY_VERSION,
        'mode': settings.COMMUNITY_MODERATION_MODE,
    }
    job = related.create(revision=obj.content_revision, payload=payload)
    if not is_post:
        sync_comment_count(obj.post_id)
    return job


def sync_comment_count(post_id):
    CommunityPost.objects.filter(pk=post_id).update(comment_count=public_comments().filter(post_id=post_id).count())


def publish_locked(obj):
    is_post = isinstance(obj, CommunityPost)
    if not approved(obj) or (obj.is_hidden if is_post else not post_public(obj.post)):
        return
    if obj.first_published_at is None:
        owner_id = obj.owner_id if is_post else obj.author_id
        if owner_id:
            grant_points(user_id=owner_id, **grant_definition(POST if is_post else COMMENT, str(obj.pk)))
        obj.first_published_at = timezone.now()
        obj.save(update_fields=('first_published_at',))
    if is_post:
        for comment in obj.comments.select_for_update().filter(moderation_status='approved', first_published_at__isnull=True):
            comment.post = obj
            publish_locked(comment)
        sync_comment_count(obj.pk)


def apply_decision_locked(obj, job):
    states = {item['resolution'] for item in job.findings}
    obj.moderation_status = 'rejected' if 'confirmed' in states else 'review' if 'open' in states else 'approved'
    obj.approved_revision = obj.content_revision if obj.moderation_status == 'approved' else None
    obj.save(update_fields=('moderation_status', 'approved_revision'))
    publish_locked(obj)
    if isinstance(obj, CommunityComment):
        sync_comment_count(obj.post_id)


@transaction.atomic
def review_job(job_id, actor, revision, review_revision, finding_ids, action, note):
    if not actor.is_authenticated or not actor.is_active or not actor.is_staff:
        raise PermissionDenied()
    reference = ModerationJob.objects.get(pk=job_id)
    obj = locked_target(reference)
    job = ModerationJob.objects.select_for_update().get(pk=job_id)
    if obj is None or obj.content_revision != revision or job.revision != revision or job.review_revision != review_revision:
        raise ModerationConflict()
    if job.state != 'done':
        raise ModerationConflict('검사가 완료된 작업만 검토할 수 있습니다.')
    selected = set(finding_ids)
    if action not in ('dismiss', 'confirm') or not selected or not selected <= {f['id'] for f in job.findings}:
        raise ValidationError('올바른 검토 사유를 선택해 주세요.')
    if not note.strip() or len(note) > 500:
        raise ValidationError('검토 근거를 1~500자로 입력해 주세요.')
    for item in job.findings:
        if item['id'] in selected:
            item['resolution'] = 'dismissed' if action == 'dismiss' else 'confirmed'
    job.review_revision += 1
    job.save(update_fields=('findings', 'review_revision', 'updated_at'))
    ModerationReview.objects.create(job=job, actor=actor, action=action, finding_ids=sorted(selected), note=note.strip())
    apply_decision_locked(obj, job)
    return job


@transaction.atomic
def retry_job(job_id, actor):
    if not actor.is_authenticated or not actor.is_active or not actor.is_staff:
        raise PermissionDenied()
    ref = ModerationJob.objects.get(pk=job_id)
    obj = locked_target(ref)
    job = ModerationJob.objects.select_for_update().get(pk=job_id)
    if obj is None or job.revision != obj.content_revision or job.state != 'failed':
        raise ModerationConflict('현재 버전의 실패한 검사만 다시 시도할 수 있습니다.')
    return enqueue_locked(obj, enforce_limit=False)
