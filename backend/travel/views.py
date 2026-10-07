from hashlib import sha256
from uuid import UUID

import secrets

from django.contrib.auth.hashers import check_password, make_password
from django.db import transaction
from django.db.models import F
from django.shortcuts import get_object_or_404
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, OpenApiTypes, extend_schema, extend_schema_view
from rest_framework import generics, status
from rest_framework.exceptions import APIException, PermissionDenied
from rest_framework.parsers import JSONParser
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import SimpleRateThrottle
from rest_framework.views import APIView

from .models import Course, CourseReaction, CourseView
from .course_ranking import STADIUM_ALIASES, filter_courses
from .directions_provider import DirectionsError, fetch_directions
from .serializers import (
    CourseCreateRequestSerializer,
    CourseCreateResultSerializer,
    CoursePatchRequestSerializer,
    CourseReactionRequestSerializer,
    CourseReactionSerializer,
    CourseResponseSerializer,
    CourseSerializer,
    CourseViewResultSerializer,
    DirectionsErrorSerializer,
    DirectionsRequestSerializer,
    DirectionsResponseSerializer,
)


EDIT_TOKEN_HEADER = OpenApiParameter(
    "X-Course-Edit-Token", OpenApiTypes.STR, OpenApiParameter.HEADER,
    required=True, description="코스 생성 응답에서 한 번만 반환되는 익명 편집 토큰",
)


class CoursePayloadTooLarge(APIException):
    status_code = 413
    default_detail = "코스 내용이 너무 깁니다."


class CourseWriteProtectionMixin:
    parser_classes = (JSONParser,)

    def initial(self, request, *args, **kwargs):
        if request.method in {"POST", "PATCH", "DELETE"}:
            origin = request.headers.get("Origin")
            if request.headers.get("Sec-Fetch-Site", "").lower() == "cross-site" or (
                origin is not None and origin != f"{request.scheme}://{request.get_host()}"
            ):
                raise PermissionDenied("같은 사이트에서 요청해 주세요.")
            content_length = request.META.get("CONTENT_LENGTH", "")
            if (content_length and not content_length.isdecimal()) or int(content_length or 0) > 256000 or len(request.body) > 256000:
                raise CoursePayloadTooLarge()
        return super().initial(request, *args, **kwargs)


class CourseWriteThrottle(SimpleRateThrottle):
    scope = "course_write"

    def get_cache_key(self, request, view):
        return self.cache_format % {"scope": self.scope, "ident": self.get_ident(request)}


@extend_schema_view(
    get=extend_schema(
        parameters=[
            OpenApiParameter("stadium", OpenApiTypes.STR, enum=tuple(STADIUM_ALIASES)),
            OpenApiParameter("ordering", OpenApiTypes.STR, enum=("newest", "likes")),
            OpenApiParameter("limit", {"type": "integer", "minimum": 1, "maximum": 100}),
            OpenApiParameter("exclude_samples", OpenApiTypes.STR, enum=("true", "false")),
        ],
        responses={200: CourseResponseSerializer(many=True), 400: OpenApiTypes.OBJECT}, auth=[],
    ),
    post=extend_schema(
        request=CourseCreateRequestSerializer,
        responses={201: CourseCreateResultSerializer, 400: OpenApiTypes.OBJECT, 403: OpenApiTypes.OBJECT, 413: OpenApiTypes.OBJECT, 429: OpenApiTypes.OBJECT},
        auth=[],
    ),
)
class CourseListCreateView(CourseWriteProtectionMixin, generics.ListCreateAPIView):
    queryset = Course.objects.prefetch_related("stops")
    serializer_class = CourseSerializer
    permission_classes = (IsAuthenticated,)

    def get_queryset(self):
        return filter_courses(super().get_queryset(), self.request.query_params)

    def get_permissions(self):
        return [AllowAny()] if self.request.method in {"GET", "HEAD", "OPTIONS"} else [IsAuthenticated()]

    def get_throttles(self):
        return [CourseWriteThrottle()] if self.request.method == "POST" else []

    def create(self, request, *args, **kwargs):
        token = secrets.token_urlsafe(32)
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        serializer.save(edit_token_hash=make_password(token), author=request.user.nickname or request.user.username)
        data = dict(serializer.data)
        data["editToken"] = token
        return Response(data, status=status.HTTP_201_CREATED)


@extend_schema_view(
    get=extend_schema(responses={200: CourseResponseSerializer, 404: OpenApiTypes.OBJECT}, auth=[]),
    patch=extend_schema(
        request=CoursePatchRequestSerializer,
        responses={200: CourseResponseSerializer, 400: OpenApiTypes.OBJECT, 403: OpenApiTypes.OBJECT, 404: OpenApiTypes.OBJECT, 413: OpenApiTypes.OBJECT, 429: OpenApiTypes.OBJECT},
        parameters=[EDIT_TOKEN_HEADER], auth=[],
    ),
    delete=extend_schema(
        responses={204: OpenApiResponse(description="본문 없음"), 403: OpenApiTypes.OBJECT, 404: OpenApiTypes.OBJECT, 413: OpenApiTypes.OBJECT, 429: OpenApiTypes.OBJECT},
        parameters=[EDIT_TOKEN_HEADER], auth=[],
    ),
)
class CourseDetailView(CourseWriteProtectionMixin, generics.RetrieveUpdateDestroyAPIView):
    queryset = Course.objects.prefetch_related("stops")
    serializer_class = CourseSerializer
    permission_classes = (IsAuthenticated,)
    http_method_names = ("get", "patch", "delete", "options")

    def get_permissions(self):
        return [AllowAny()] if self.request.method in {"GET", "HEAD", "OPTIONS"} else [IsAuthenticated()]

    def get_throttles(self):
        return [CourseWriteThrottle()] if self.request.method in {"PATCH", "DELETE"} else []

    def check_edit_token(self, course):
        token = self.request.headers.get("X-Course-Edit-Token", "")
        if not token or not check_password(token, course.edit_token_hash):
            raise PermissionDenied("올바른 코스 편집 토큰이 필요합니다.")

    def update(self, request, *args, **kwargs):
        self.check_edit_token(self.get_object())
        return super().update(request, *args, **kwargs)

    def destroy(self, request, *args, **kwargs):
        self.check_edit_token(self.get_object())
        return super().destroy(request, *args, **kwargs)


class CourseReactionView(CourseWriteProtectionMixin, APIView):
    permission_classes = (IsAuthenticated,)

    def get_throttles(self):
        return [CourseWriteThrottle()] if self.request.method == "POST" else []

    @extend_schema(responses={200: CourseReactionSerializer, 401: OpenApiTypes.OBJECT, 404: OpenApiTypes.OBJECT})
    def get(self, request, pk):
        course = get_object_or_404(Course, pk=pk)
        liked = CourseReaction.objects.filter(course=course, user=request.user).exists()
        return Response(CourseReactionSerializer({"liked": liked, "likes": course.likes}).data)

    @extend_schema(
        request=CourseReactionRequestSerializer,
        responses={200: CourseReactionSerializer, 400: OpenApiTypes.OBJECT, 401: OpenApiTypes.OBJECT, 403: OpenApiTypes.OBJECT, 404: OpenApiTypes.OBJECT, 413: OpenApiTypes.OBJECT, 429: OpenApiTypes.OBJECT},
        description="JWT 회원 단위로 원하는 좋아요 상태를 멱등 적용합니다.",
    )
    @transaction.atomic
    def post(self, request, pk):
        serializer = CourseReactionRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        desired = serializer.validated_data["liked"]
        course = get_object_or_404(Course.objects.select_for_update(), pk=pk)
        reaction = CourseReaction.objects.filter(course=course, user=request.user).first()
        if desired and not reaction:
            CourseReaction.objects.create(course=course, user=request.user)
            Course.objects.filter(pk=course.pk).update(likes=F("likes") + 1)
        elif not desired and reaction:
            reaction.delete()
            Course.objects.filter(pk=course.pk).update(likes=F("likes") - 1)
        course.refresh_from_db(fields=("likes",))
        data = CourseReactionSerializer({"liked": desired, "likes": course.likes}).data
        return Response(data)


class CourseViewView(CourseWriteProtectionMixin, APIView):
    permission_classes = (AllowAny,)
    throttle_classes = (CourseWriteThrottle,)

    @extend_schema(
        request=None,
        responses={200: CourseViewResultSerializer, 400: OpenApiTypes.OBJECT, 403: OpenApiTypes.OBJECT, 404: OpenApiTypes.OBJECT, 413: OpenApiTypes.OBJECT, 429: OpenApiTypes.OBJECT},
        parameters=[OpenApiParameter("X-Course-View-Token", OpenApiTypes.UUID, OpenApiParameter.HEADER, required=True)],
        auth=[], description="익명 브라우저가 보낸 UUID capability마다 코스 조회를 한 번만 집계합니다. 이는 사람이나 계정 식별자가 아닙니다.",
    )
    @transaction.atomic
    def post(self, request, pk):
        token = request.headers.get("X-Course-View-Token", "")
        try:
            token = str(UUID(token))
        except (ValueError, AttributeError):
            return Response({"detail": "올바른 조회 토큰이 필요합니다."}, status=status.HTTP_400_BAD_REQUEST)
        course = get_object_or_404(Course.objects.select_for_update(), pk=pk)
        _, created = CourseView.objects.get_or_create(course=course, actor_digest=sha256(token.encode()).hexdigest())
        if created:
            Course.objects.filter(pk=course.pk).update(views=F("views") + 1)
        course.refresh_from_db(fields=("views",))
        data = CourseViewResultSerializer({"views": course.views}).data
        return Response(data)


class ExternalRequestMixin:
    parser_classes = (JSONParser,)
    permission_classes = (AllowAny,)

    def initial(self, request, *args, **kwargs):
        length = request.META.get("CONTENT_LENGTH", "")
        if (length and not length.isdecimal()) or int(length or 0) > 12000 or len(request.body) > 12000:
            raise CoursePayloadTooLarge("요청 내용이 너무 큽니다.")
        return super().initial(request, *args, **kwargs)


class DirectionsView(ExternalRequestMixin, APIView):
    @extend_schema(request=DirectionsRequestSerializer, responses={200: DirectionsResponseSerializer, 400: DirectionsErrorSerializer, 413: DirectionsErrorSerializer, 415: DirectionsErrorSerializer, 429: DirectionsErrorSerializer, 502: DirectionsErrorSerializer, 503: DirectionsErrorSerializer}, auth=[])
    def post(self, request):
        serializer = DirectionsRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        values = dict(serializer.validated_data)
        values.pop("action", None)
        try:
            return Response(fetch_directions(**values))
        except DirectionsError as exc:
            return Response({"error": "지도 데이터 연결 설정이 필요해요." if exc.status == 503 else "길찾기 조회에 실패했어요."}, status=exc.status)
