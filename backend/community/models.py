import uuid

from django.conf import settings
from django.db import connection, models
from django.db.models import Q
from django.utils import timezone


TEAM_CODES = ("LG", "HH", "SK", "SS", "NC", "KT", "LT", "HT", "OB", "WO")
FREE_CATEGORIES = ("질문", "잡담")
TEAM_CATEGORIES = FREE_CATEGORIES + ("응원", "경기토론", "전력토론", "소식·정보", "이적·신인", "직관후기", "좌석·예매", "직관준비", "굿즈", "사진·영상")


def new_post_source_id():
    return uuid.uuid4().hex


def next_post_number():
    with connection.cursor() as cursor:
        cursor.execute("SELECT nextval('community_post_number_seq')")
        return f"{cursor.fetchone()[0]:06d}"


class ModeratedContent(models.Model):
    moderation_status = models.CharField(max_length=10, default="pending", choices=[(s, s) for s in ("pending", "approved", "review", "rejected", "error")], db_index=True)
    content_revision = models.PositiveIntegerField(default=0)
    approved_revision = models.PositiveIntegerField(null=True, blank=True)
    first_published_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        abstract = True


class CommunityPost(ModeratedContent):
    source_id = models.CharField(max_length=40, primary_key=True, default=new_post_source_id, editable=False)
    post_number = models.CharField(max_length=6, unique=True, default=next_post_number, editable=False)
    board = models.CharField(max_length=8, choices=(("free", "free"), ("teams", "teams")))
    team_code = models.CharField(max_length=2, blank=True)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="community_posts",
    )
    idempotency_key = models.CharField(max_length=128, null=True, blank=True)
    author = models.CharField(max_length=80)
    title = models.CharField(max_length=200)
    content = models.TextField()
    content_doc = models.JSONField(null=True, blank=True)
    category = models.CharField(max_length=20)
    created_at = models.DateTimeField(null=True, blank=True, default=timezone.now)
    views = models.PositiveIntegerField(default=0)
    recommendations = models.PositiveIntegerField(default=0)
    comment_count = models.PositiveIntegerField(default=0)
    is_sample = models.BooleanField(default=False)
    # 신고 처리로 숨긴 글: 공개 목록·상세에서 보이지 않는다
    is_hidden = models.BooleanField(default=False)
    is_trade = models.BooleanField(default=False)
    is_advertisement = models.BooleanField(default=False)
    submission_hash = models.CharField(max_length=64, blank=True, editable=False)

    class Meta:
        ordering = ("post_number",)
        constraints = (
            models.CheckConstraint(
                condition=Q(post_number__regex=r"^[0-9]{6}$", post_number__gte="000001"),
                name="community_post_number_valid",
            ),
            models.CheckConstraint(
                condition=Q(board="free", team_code="") | Q(board="teams", team_code__in=TEAM_CODES),
                name="community_post_board_team_valid",
            ),
            models.UniqueConstraint(
                fields=("owner", "idempotency_key"),
                condition=Q(owner__isnull=False, idempotency_key__isnull=False),
                name="community_post_owner_idempotency_unique",
            ),
        )


class CommunityDraft(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="community_drafts")
    board = models.CharField(max_length=8, choices=(("free", "free"), ("teams", "teams")))
    team_code = models.CharField(max_length=2, blank=True, default="")
    category = models.CharField(max_length=20, blank=True, default="")
    title = models.CharField(max_length=200, blank=True, default="")
    content = models.TextField(max_length=20000, blank=True, default="")
    revision = models.PositiveIntegerField(default=1)
    is_trade = models.BooleanField(default=False)
    is_advertisement = models.BooleanField(default=False)
    published_post = models.ForeignKey(
        CommunityPost,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="source_drafts",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = (
            models.CheckConstraint(
                condition=Q(board="free", team_code="") | Q(board="teams", team_code__in=TEAM_CODES),
                name="community_draft_board_team_valid",
            ),
            models.CheckConstraint(condition=Q(revision__gte=1), name="community_draft_revision_valid"),
        )

    @property
    def image_ids(self):
        return list(self.images.values_list("id", flat=True))


class CommunityImage(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="community_images",
    )
    object_key = models.CharField(max_length=255, unique=True)
    content_type = models.CharField(max_length=20)
    size = models.PositiveIntegerField()
    width = models.PositiveIntegerField()
    height = models.PositiveIntegerField()
    created_at = models.DateTimeField(auto_now_add=True)
    draft = models.ForeignKey(CommunityDraft, null=True, blank=True, on_delete=models.SET_NULL, related_name="images")
    post = models.ForeignKey(CommunityPost, null=True, blank=True, on_delete=models.SET_NULL, related_name="images")
    course = models.ForeignKey("travel.Course", null=True, blank=True, on_delete=models.SET_NULL, related_name="images")

    class Meta:
        ordering = ("created_at", "id")
        constraints = (
            models.CheckConstraint(
                condition=(
                    (Q(draft__isnull=True) & Q(post__isnull=True))
                    | (Q(draft__isnull=True) & Q(course__isnull=True))
                    | (Q(post__isnull=True) & Q(course__isnull=True))
                ),
                name="community_image_single_target",
            ),
        )


class CommunityComment(ModeratedContent):
    post = models.ForeignKey(CommunityPost, on_delete=models.CASCADE, related_name="comments")
    author = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="community_comments")
    content = models.TextField(max_length=2000)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("created_at", "pk")


class CommunityVote(models.Model):
    post = models.ForeignKey(CommunityPost, on_delete=models.CASCADE, related_name="votes")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="community_votes")
    value = models.CharField(max_length=4, choices=(("up", "up"), ("down", "down")))

    class Meta:
        constraints = (
            models.UniqueConstraint(fields=("post", "user"), name="community_vote_post_user_unique"),
            models.CheckConstraint(condition=Q(value__in=("up", "down")), name="community_vote_value_valid"),
        )


class CommunityReport(models.Model):
    post = models.ForeignKey(CommunityPost, on_delete=models.CASCADE, related_name="reports")
    reporter = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="community_reports")
    reason = models.CharField(
        max_length=13,
        choices=(("spam", "spam"), ("abuse", "abuse"), ("inappropriate", "inappropriate"), ("privacy", "privacy"), ("other", "other")),
    )
    detail = models.CharField(max_length=50, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    # 관리자 처리 상태: 대기 / 보류 / 숨김 (삭제하면 글과 함께 신고도 지워진다)
    status = models.CharField(
        max_length=8,
        choices=(("pending", "pending"), ("held", "held"), ("hidden", "hidden")),
        default="pending",
    )
    handled_at = models.DateTimeField(null=True, blank=True)
    handled_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="handled_community_reports",
    )

    class Meta:
        constraints = (
            models.UniqueConstraint(fields=("post", "reporter"), name="community_report_post_reporter_unique"),
            models.CheckConstraint(
                condition=Q(reason__in=("spam", "abuse", "inappropriate", "privacy", "other")),
                name="community_report_reason_valid",
            ),
        )


class PredictionGame(models.Model):
    STATUS_CHOICES = (
        ("scheduled", "scheduled"), ("live", "live"), ("final", "final"),
        ("cancelled", "cancelled"), ("postponed", "postponed"),
        ("suspended", "suspended"), ("unknown", "unknown"),
    )
    RESULT_CHOICES = (("", "pending"), ("home", "home"), ("away", "away"), ("draw", "draw"))

    source_id = models.CharField(max_length=64, primary_key=True)
    game_date = models.DateField(db_index=True)
    starts_at = models.DateTimeField(null=True, blank=True)
    stadium = models.CharField(max_length=80, blank=True)
    away_team_code = models.CharField(max_length=2)
    away_team_name = models.CharField(max_length=20)
    home_team_code = models.CharField(max_length=2)
    home_team_name = models.CharField(max_length=20)
    away_score = models.PositiveSmallIntegerField(null=True, blank=True)
    home_score = models.PositiveSmallIntegerField(null=True, blank=True)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES)
    result = models.CharField(max_length=4, choices=RESULT_CHOICES, blank=True, default="")
    source_fetched_at = models.DateTimeField()
    locked_at = models.DateTimeField(null=True, blank=True)
    voided_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = (
            models.CheckConstraint(condition=Q(home_team_code__in=TEAM_CODES), name="community_prediction_home_team_valid"),
            models.CheckConstraint(condition=Q(away_team_code__in=TEAM_CODES), name="community_prediction_away_team_valid"),
            models.CheckConstraint(condition=~Q(home_team_code=models.F("away_team_code")), name="community_prediction_teams_distinct"),
            models.CheckConstraint(condition=Q(status__in=("scheduled", "live", "final", "cancelled", "postponed", "suspended", "unknown")), name="community_prediction_status_valid"),
            models.CheckConstraint(condition=Q(result__in=("", "home", "away", "draw")), name="community_prediction_result_valid"),
        )


class GamePrediction(models.Model):
    CHOICE_CHOICES = (("home", "home"), ("away", "away"))

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="game_predictions")
    game = models.ForeignKey(PredictionGame, on_delete=models.CASCADE, related_name="predictions")
    choice = models.CharField(max_length=4, choices=CHOICE_CHOICES)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = (
            models.UniqueConstraint(fields=("user", "game"), name="community_prediction_user_game_unique"),
            models.CheckConstraint(condition=Q(choice__in=("home", "away")), name="community_prediction_choice_valid"),
        )

from .moderation_models import ModerationAdmission, ModerationJob, ModerationReview  # noqa: E402,F401
