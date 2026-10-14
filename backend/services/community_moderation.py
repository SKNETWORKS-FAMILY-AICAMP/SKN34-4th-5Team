"""Load ORM jobs, request checks, and persist revision-safe decisions.

ModerationJob references the existing CommunityPost or CommunityComment model.
Requests use the submitted snapshot; finish locks the current ORM target and
rejects stale revisions before applying findings and publication state.
"""
import uuid
from datetime import timedelta
from django.conf import settings
from django.db import DatabaseError, transaction
from django.db.models import Exists, OuterRef, Q
from django.utils import timezone
from community.models import CommunityPost, CommunityComment, ModerationJob
from community.moderation_service import locked_target, apply_decision_locked, enqueue_locked


def enqueue_unchecked():
    """Backfill absent current-revision jobs; preserve existing results/retry limits.

Materialize candidate IDs before enqueueing so this is a finite scan. Recheck
under the same parent-before-comment locks as normal submission and review.
Cancelled jobs may be replaced; failed jobs require the existing explicit retry.
"""
    counts = {'posts': 0, 'comments': 0}
    for model, relation, count_key in (
        (CommunityPost, 'post_id', 'posts'),
        (CommunityComment, 'comment_id', 'comments'),
    ):
        current_jobs = ModerationJob.objects.filter(
            **{relation: OuterRef('pk')}, revision=OuterRef('content_revision'),
        ).exclude(state='cancelled')
        candidates = list(model.objects.annotate(has_job=Exists(current_jobs))
                          .filter(has_job=False).values_list('pk', flat=True))
        for pk in candidates:
            with transaction.atomic():
                ref = ModerationJob(**{relation: pk})
                obj = locked_target(ref)
                if obj is None or obj.moderation_jobs.filter(
                    revision=obj.content_revision,
                ).exclude(state='cancelled').exists():
                    continue
                enqueue_locked(obj, enforce_limit=False)
                counts[count_key] += 1
    return counts


def enqueue_report_locked(post):
    """Caller holds the post lock. Reuse in-flight report checks and keep prior visibility."""
    post.is_reviewed = False
    post.save(update_fields=['is_reviewed'])
    active = post.moderation_jobs.filter(
        revision=post.content_revision, state__in=('queued', 'running'), payload__trigger='report',
    ).first()
    if active is not None:
        return active
    # A report is not a content edit: keep the revision, decisions and other leases.
    return enqueue_locked(post, enforce_limit=False, mode='openai', trigger='report')


@transaction.atomic
def claim(job_id=None):
    now = timezone.now()
    jobs = ModerationJob.objects.select_for_update(skip_locked=True).filter(
        Q(state='queued', available_at__lte=now) | Q(state='running', lease_until__lte=now)
    )
    if job_id is not None:
        jobs = jobs.filter(pk=job_id)
    job = jobs.order_by('available_at', 'pk').first()
    if job is None:
        return None
    job.state, job.lease_token = 'running', uuid.uuid4()
    job.attempts += 1
    job.lease_until = now + timedelta(seconds=settings.COMMUNITY_MODERATION_LEASE_SECONDS)
    job.save()
    return job


@transaction.atomic
def finish(ref, result=None, error=None):
    obj = locked_target(ref)
    job = ModerationJob.objects.select_for_update().filter(pk=ref.pk).first()
    if job is None or job.state != 'running' or job.lease_token != ref.lease_token:
        return False
    job.lease_token = job.lease_until = None
    if obj is None or obj.content_revision != job.revision:
        job.state = 'cancelled'
    elif error is not None and job.payload['mode'] == 'openai':
        if isinstance(error, DatabaseError):
            raise error
        from .community_moderation_policy import finding
        job.error_code = 'automatic_check_failed'
        job.findings = [finding('manual_review', '', '자동 검사 실패: 운영진의 수동 검토가 필요합니다.')]
        if job.payload['image_ids']:
            job.findings.append(finding('image_review', '', '첨부 이미지는 자동 검사에 포함되지 않아 운영진 확인이 필요합니다.'))
        job.evidence = {'mode': 'openai', 'policy_version': job.payload['policy_version'],
                        'fallback': 'manual_review', 'error_code': job.error_code}
        job.state = 'done'
        apply_decision_locked(obj, job)
    elif error is not None:
        terminal = job.attempts >= settings.COMMUNITY_MODERATION_MAX_ATTEMPTS
        job.state = 'failed' if terminal else 'queued'
        job.available_at = timezone.now() + timedelta(seconds=min(300, 10 * 2 ** job.attempts))
        job.error_code = type(error).__name__[:80]
        obj.moderation_status = 'error' if terminal else 'pending'
        obj.save(update_fields=('moderation_status',))
    else:
        job.findings, job.evidence = result
        job.state, job.error_code = 'done', ''
        apply_decision_locked(obj, job)
    job.save()
    return True


def run_one(checker=None, *, job_id=None):
    from .community_moderation_policy import check
    job = claim(job_id=job_id)
    if job is None:
        return False
    try:
        if job.attempts > settings.COMMUNITY_MODERATION_MAX_ATTEMPTS:
            raise RuntimeError('attempts exhausted')
        result = (checker or check)(job.payload)
    except DatabaseError:
        raise
    except Exception as error:
        finish(job, error=error)
    else:
        finish(job, result=result)
    return True
