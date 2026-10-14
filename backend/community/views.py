from django.db import DataError, IntegrityError, transaction
from django.db.models import Count, F, Q
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, OpenApiTypes, PolymorphicProxySerializer, extend_schema, extend_schema_view
from rest_framework import generics, status
from rest_framework.exceptions import NotAuthenticated, PermissionDenied, ValidationError
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from accounts.activity import activity_author
from accounts.point_policy import POST_READ, grant_definition
from accounts.point_service import grant_points

from .models import CommunityPost, TEAM_CODES, TEAM_CATEGORIES
from .pagination import CommunityPostPageSerializer, CommunityPostPagination, CommunityPostQuery
from .serializers import CommunityPostPatchSerializer, CommunityPostSerializer, CommunityPostWriteSerializer
from .moderation_service import enqueue_locked, submission_hash
from .visibility import public_posts


POST_INPUT_FIELDS = ("board", "team_code", "category", "title", "content", "content_doc", "is_trade", "is_advertisement")


def post_queryset():
    return CommunityPost.objects.select_related("owner__point_wallet").prefetch_related("images").annotate(
        upvote_count=Count("votes", filter=Q(votes__value="up"), distinct=True),
        downvote_count=Count("votes", filter=Q(votes__value="down"), distinct=True),
        actual_comment_count=Count("comments", filter=Q(comments__approved_revision=F('comments__content_revision')), distinct=True),
    ).order_by("-post_number")


def same_submission(post, validated_data):
    if post.submission_hash:
        return post.submission_hash == submission_hash(validated_data)
    return all(getattr(post, field) == validated_data.get(field, False if field.startswith('is_') else None) for field in POST_INPUT_FIELDS)


@extend_schema_view(
    get=extend_schema(
        parameters=[
            OpenApiParameter("board", OpenApiTypes.STR, enum=("free", "teams")),
            OpenApiParameter("category", OpenApiTypes.STR, enum=TEAM_CATEGORIES, description="글 분류 정확히 일치. 생략하면 전체 분류."),
            OpenApiParameter("team", OpenApiTypes.STR),
            OpenApiParameter("mine", OpenApiTypes.STR, enum=("1",)),
            OpenApiParameter("author_id", {"type": "integer", "minimum": 1}, description="작성자 활동 목록. JWT 인증 필요, 본인 또는 공개 설정된 회원만 조회 가능."),
            OpenApiParameter("page", {"type": "integer", "minimum": 1, "maximum": 2_147_483_647}),
            OpenApiParameter("page_size", {"type": "integer", "minimum": 1, "maximum": 100}),
            OpenApiParameter("q", {"type": "string", "maxLength": 200}),
            OpenApiParameter("search_field", OpenApiTypes.STR, enum=("all", "title", "author")),
        ],
        responses={
            200: PolymorphicProxySerializer(
                component_name="CommunityPostListResponse",
                serializers=[CommunityPostSerializer(many=True), CommunityPostPageSerializer],
                resource_type_field_name=None,
                many=False,
            ),
            400: OpenApiTypes.OBJECT,
            401: OpenApiTypes.OBJECT,
            403: OpenApiTypes.OBJECT,
            404: OpenApiTypes.OBJECT,
        },
    ),
    post=extend_schema(
        request=CommunityPostWriteSerializer,
        parameters=[OpenApiParameter("Idempotency-Key", OpenApiTypes.STR, OpenApiParameter.HEADER, required=True)],
        responses={200: CommunityPostSerializer, 202: CommunityPostSerializer, 400: OpenApiTypes.OBJECT, 401: OpenApiTypes.OBJECT, 409: OpenApiTypes.OBJECT},
    ),
)
class CommunityPostListCreateView(generics.ListCreateAPIView):
    serializer_class = CommunityPostSerializer
    pagination_class = CommunityPostPagination
    permission_classes = (AllowAny,)
    http_method_names = ("get", "post", "head", "options")

    def get_permissions(self):
        return (IsAuthenticated(),) if self.request.method == "POST" else (AllowAny(),)

    def get_queryset(self):
        # 신고 처리로 숨긴 글은 공개 목록에 나오지 않는다
        queryset = public_posts(post_queryset())
        if "author_id" in self.request.query_params:
            queryset = queryset.filter(owner=activity_author(self.request))
        query = CommunityPostQuery.from_params(self.request.query_params)
        board = self.request.query_params.get("board")
        team = self.request.query_params.get("team")
        mine = self.request.query_params.get("mine")
        if board is not None and board not in {"free", "teams"}:
            raise ValidationError({"board": "free 또는 teams를 입력해 주세요."})
        if team is not None:
            team = team.upper()
            if team not in TEAM_CODES:
                raise ValidationError({"team": "올바른 팀 코드를 입력해 주세요."})
            if board == "free":
                raise ValidationError({"team": "팀 필터는 teams 게시판에서만 사용할 수 있습니다."})
            queryset = queryset.filter(team_code=team)
        if mine is not None:
            if mine != "1":
                raise ValidationError({"mine": "mine은 1만 사용할 수 있습니다."})
            if not self.request.user.is_authenticated:
                raise NotAuthenticated()
            queryset = queryset.filter(owner=self.request.user)
        if board is not None:
            queryset = queryset.filter(board=board)
        if query.q:
            fields = {
                "all": Q(title__icontains=query.q) | Q(content__icontains=query.q),
                "title": Q(title__icontains=query.q),
                "author": Q(author__icontains=query.q),
            }
            queryset = queryset.filter(fields[query.search_field])
        if query.category is not None:
            queryset = queryset.filter(category=query.category)
        return queryset

    def create(self, request, *args, **kwargs):
        key = request.headers.get("Idempotency-Key", "").strip()
        if not key or len(key) > 128:
            raise ValidationError({"idempotencyKey": "1~128자의 Idempotency-Key가 필요합니다."})

        existing = CommunityPost.objects.filter(owner=request.user, idempotency_key=key).first()
        # A successful idempotent retry must remain valid after later edits/image moves.
        if existing:
            # Reuse field normalization, without revalidating moved image ownership.
            incoming = self.get_serializer().to_internal_value(request.data)
            incoming['team_code'] = incoming['team_code'].upper()
            if not same_submission(existing, incoming):
                return Response({'idempotencyKey': '같은 키로 다른 게시글을 만들 수 없습니다.'}, status=409)
            return Response(self.get_serializer(post_queryset().get(pk=existing.pk)).data)
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        values = serializer.validated_data
        try:
            with transaction.atomic():
                post = serializer.save(
                    owner=request.user,
                    author=request.user.nickname or request.user.username,
                    idempotency_key=key,
                    is_sample=False,
                    submission_hash=submission_hash(values),
                )
                enqueue_locked(post)
        except IntegrityError:
            existing = CommunityPost.objects.filter(owner=request.user, idempotency_key=key).first()
            if existing and same_submission(existing, values):
                return Response(self.get_serializer(post_queryset().get(pk=existing.pk)).data)
            return Response({"idempotencyKey": "같은 키로 다른 게시글을 만들 수 없습니다."}, status=status.HTTP_409_CONFLICT)
        except DataError as exc:
            raise ValidationError({"postNumber": "게시글 번호를 더 발급할 수 없습니다."}) from exc

        post = post_queryset().get(pk=post.pk)
        return Response(self.get_serializer(post).data, status=status.HTTP_202_ACCEPTED)


@extend_schema_view(
    get=extend_schema(responses={200: CommunityPostSerializer, 404: OpenApiTypes.OBJECT}),
    patch=extend_schema(
        request=CommunityPostPatchSerializer,
        responses={202: CommunityPostSerializer, 400: OpenApiTypes.OBJECT, 401: OpenApiTypes.OBJECT, 403: OpenApiTypes.OBJECT, 404: OpenApiTypes.OBJECT},
    ),
    delete=extend_schema(responses={204: OpenApiResponse(description="본문 없음"), 401: OpenApiTypes.OBJECT, 403: OpenApiTypes.OBJECT, 404: OpenApiTypes.OBJECT}),
)
class CommunityPostDetailView(generics.RetrieveUpdateDestroyAPIView):
    serializer_class = CommunityPostSerializer
    lookup_field = "source_id"
    lookup_url_kwarg = "source_id"
    http_method_names = ("get", "patch", "delete", "head", "options")

    def get_permissions(self):
        return (AllowAny(),) if self.request.method in {"GET", "HEAD", "OPTIONS"} else (IsAuthenticated(),)

    @transaction.atomic
    def retrieve(self, request, *args, **kwargs):
        from django.shortcuts import get_object_or_404
        # Keep the public check, view counter and serialization on one revision.
        get_object_or_404(public_posts(CommunityPost.objects.select_for_update()), source_id=kwargs['source_id'])
        response = super().retrieve(request, *args, **kwargs)
        response['Cache-Control'] = 'no-store'
        return response

    def get_queryset(self):
        # 숨긴 글은 관리자만 열람할 수 있다 (작성자의 수정·삭제는 그대로 허용)
        if self.request.method in {"GET", "HEAD", "OPTIONS"}:
            return public_posts(post_queryset())
        return post_queryset()

    def update(self, request, *args, **kwargs):
        from django.shortcuts import get_object_or_404
        with transaction.atomic():
            post = get_object_or_404(CommunityPost.objects.select_for_update(), source_id=kwargs['source_id'])
            if post.owner_id != request.user.id:
                raise PermissionDenied('작성자만 수정할 수 있습니다.')
            serializer = self.get_serializer(post, data=request.data, partial=True)
            serializer.is_valid(raise_exception=True)
            post = serializer.save()
            enqueue_locked(post)
        return Response(self.get_serializer(post_queryset().get(pk=post.pk)).data, status=202)

    def get_object(self):
        post = super().get_object()
        if self.request.method not in {"GET", "HEAD", "OPTIONS"} and post.owner_id != self.request.user.id:
            raise PermissionDenied("작성자만 수정하거나 삭제할 수 있습니다.")
        if self.request.method == "GET":
            CommunityPost.objects.filter(pk=post.pk).update(views=F("views") + 1)
            if self.request.user.is_authenticated and post.owner_id != self.request.user.id:
                grant_points(user=self.request.user, **grant_definition(POST_READ, f"{post.source_id}"))
            post = post_queryset().get(pk=post.pk)
        return post
