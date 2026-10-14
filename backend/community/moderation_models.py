from django.conf import settings
from django.db import models
from django.db.models import Q
from django.utils import timezone


class ModerationSettings(models.Model):
    id = models.PositiveSmallIntegerField(primary_key=True, default=1, editable=False)
    mode = models.CharField(max_length=10, choices=(('manual', 'manual'), ('openai', 'openai')), default='openai')
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.CheckConstraint(condition=Q(id=1), name='community_mode_singleton'),
            models.CheckConstraint(condition=Q(mode__in=('manual', 'openai')), name='community_mode_valid'),
        ]


class ModerationAdmission(models.Model):
    account = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        indexes = [models.Index(fields=('account', 'created_at'), name='community_admission_hour')]


class ModerationJob(models.Model):
    post = models.ForeignKey('community.CommunityPost', null=True, blank=True, on_delete=models.CASCADE, related_name='moderation_jobs')
    comment = models.ForeignKey('community.CommunityComment', null=True, blank=True, on_delete=models.CASCADE, related_name='moderation_jobs')
    revision = models.PositiveIntegerField()
    payload = models.JSONField()
    state = models.CharField(max_length=10, default='queued', choices=[(s, s) for s in ('queued', 'running', 'done', 'failed', 'cancelled')])
    attempts = models.PositiveIntegerField(default=0)
    available_at = models.DateTimeField(default=timezone.now)
    lease_token = models.UUIDField(null=True, blank=True)
    lease_until = models.DateTimeField(null=True, blank=True)
    findings = models.JSONField(default=list)
    evidence = models.JSONField(default=dict)
    review_revision = models.PositiveIntegerField(default=0)
    error_code = models.CharField(max_length=80, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.CheckConstraint(condition=(Q(post__isnull=False, comment__isnull=True) | Q(post__isnull=True, comment__isnull=False)), name='community_mod_one_target'),
            models.UniqueConstraint(fields=('post', 'revision'), name='community_mod_post_revision',
                                    condition=Q(payload__trigger__isnull=True) | ~Q(payload__trigger='report')),
            models.UniqueConstraint(fields=('comment', 'revision'), name='community_mod_comment_revision'),
        ]
        indexes = [models.Index(fields=('state', 'available_at'), name='community_mod_due')]


class ModerationReview(models.Model):
    job = models.ForeignKey(ModerationJob, on_delete=models.CASCADE, related_name='reviews')
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    action = models.CharField(max_length=10)
    finding_ids = models.JSONField(default=list)
    note = models.CharField(max_length=500)
    created_at = models.DateTimeField(auto_now_add=True)
