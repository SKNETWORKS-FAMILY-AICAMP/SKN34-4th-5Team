import io
import logging
import uuid
import warnings

from PIL import Image, ImageOps, UnidentifiedImageError
from django.db import transaction
from django.http import StreamingHttpResponse
from drf_spectacular.utils import OpenApiResponse, OpenApiTypes, extend_schema
from rest_framework import serializers, status
from rest_framework.parsers import MultiPartParser
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .image_schemas import image_metadata
from .image_storage import ObjectNotFound, StorageUnavailable, delete_object, get_object, put_object
from .models import CommunityImage
from .visibility import post_public


logger = logging.getLogger(__name__)
MAX_BYTES = 5 * 1024 * 1024
MAX_REQUEST_BYTES = MAX_BYTES + 64 * 1024
MAX_PIXELS = 20_000_000
FORMATS = {
    "JPEG": ("image/jpeg", "jpg", {"quality": 90, "optimize": True}),
    "PNG": ("image/png", "png", {"optimize": True}),
    "WEBP": ("image/webp", "webp", {"quality": 90, "method": 4}),
}


def _read_bounded(upload):
    chunks = []
    size = 0
    for chunk in upload.chunks():
        size += len(chunk)
        if size > MAX_BYTES:
            raise ValueError("이미지는 5MiB 이하여야 합니다.")
        chunks.append(chunk)
    return b"".join(chunks)


def _normalize(upload):
    if upload.content_type not in {item[0] for item in FORMATS.values()}:
        raise ValueError("JPEG, PNG, WebP 이미지만 업로드할 수 있습니다.")
    raw = _read_bounded(upload)
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(raw)) as image:
                if image.format not in FORMATS or getattr(image, "n_frames", 1) != 1:
                    raise ValueError("정지 JPEG, PNG, WebP 이미지만 업로드할 수 있습니다.")
                source_format = image.format
                content_type, extension, save_options = FORMATS[source_format]
                if upload.content_type != content_type:
                    raise ValueError("파일 내용과 Content-Type이 일치하지 않습니다.")
                if image.width * image.height > MAX_PIXELS:
                    raise ValueError("이미지는 2천만 픽셀 이하여야 합니다.")
                image.load()
                image = ImageOps.exif_transpose(image)
                if source_format == "JPEG" and image.mode not in {"RGB", "L"}:
                    image = image.convert("RGB")
                image.info.clear()
                width, height = image.size
                output = io.BytesIO()
                image.save(output, format=source_format, **save_options)
    except (
        Image.DecompressionBombError,
        Image.DecompressionBombWarning,
        UnidentifiedImageError,
        OSError,
        SyntaxError,
    ) as error:
        raise ValueError("손상되었거나 지원하지 않는 이미지입니다.") from error
    normalized = output.getvalue()
    if len(normalized) > MAX_BYTES:
        raise ValueError("정규화된 이미지는 5MiB 이하여야 합니다.")
    return normalized, content_type, extension, width, height


def _published(image):
    return bool(
        image.post_id
        or image.course_id
        or image.draft_id
        and image.draft.published_post_id
    )


class ImageUploadRequest(serializers.Serializer):
    image = serializers.ImageField()


class ImageUploadResponse(serializers.Serializer):
    id = serializers.UUIDField()
    contentType = serializers.ChoiceField(choices=("image/jpeg", "image/png", "image/webp"))
    size = serializers.IntegerField(min_value=1, max_value=MAX_BYTES)
    width = serializers.IntegerField(min_value=1)
    height = serializers.IntegerField(min_value=1)
    createdAt = serializers.DateTimeField()
    url = serializers.CharField()


class CommunityImageUploadView(APIView):
    permission_classes = (IsAuthenticated,)
    parser_classes = (MultiPartParser,)

    @extend_schema(
        request=ImageUploadRequest,
        responses={
            201: ImageUploadResponse,
            400: OpenApiTypes.OBJECT,
            401: OpenApiTypes.OBJECT,
            411: OpenApiTypes.OBJECT,
            413: OpenApiTypes.OBJECT,
            503: OpenApiTypes.OBJECT,
        },
    )
    def post(self, request):
        try:
            content_length = int(request.META["CONTENT_LENGTH"])
        except (KeyError, TypeError, ValueError):
            return Response({"detail": "Content-Length가 필요합니다."}, status=411)
        if content_length > MAX_REQUEST_BYTES:
            return Response({"detail": "요청 본문이 너무 큽니다."}, status=413)
        if set(request.data) != {"image"} or len(request.FILES.getlist("image")) != 1:
            return Response({"detail": "image 파일 하나만 전송할 수 있습니다."}, status=status.HTTP_400_BAD_REQUEST)
        upload = request.FILES["image"]
        try:
            body, content_type, extension, width, height = _normalize(upload)
        except ValueError as error:
            return Response({"detail": str(error)}, status=status.HTTP_400_BAD_REQUEST)

        key = f"community/{uuid.uuid4().hex[:2]}/{uuid.uuid4().hex}.{extension}"
        try:
            put_object(key, body, content_type)
        except StorageUnavailable:
            return Response({"detail": "이미지 저장소를 사용할 수 없습니다."}, status=503)
        try:
            image = CommunityImage.objects.create(
                owner=request.user,
                object_key=key,
                content_type=content_type,
                size=len(body),
                width=width,
                height=height,
            )
        except Exception:
            try:
                delete_object(key)
            except Exception:
                logger.exception("Failed to compensate object upload for %s", key)
            raise
        return Response(image_metadata(image), status=status.HTTP_201_CREATED)


class CommunityImageDetailView(APIView):
    permission_classes = (AllowAny,)

    def _get_image(self, image_id):
        return CommunityImage.objects.select_related("post", "draft__published_post").filter(pk=image_id).first()

    @extend_schema(
        responses={
            200: OpenApiResponse(response=OpenApiTypes.BINARY, description="원본 비공개 저장소에서 스트리밍한 이미지"),
            404: OpenApiTypes.OBJECT,
            503: OpenApiTypes.OBJECT,
        }
    )
    def get(self, request, image_id):
        image = self._get_image(image_id)
        if image is None:
            return Response(status=status.HTTP_404_NOT_FOUND)
        parent = image.post if image.post_id else image.draft.published_post if image.draft_id and image.draft.published_post_id else None
        published = post_public(parent) if parent else bool(image.course_id)
        if not published and (not request.user.is_authenticated or not (image.owner_id == request.user.pk or request.user.is_staff)):
            return Response(status=status.HTTP_404_NOT_FOUND)

        try:
            body = get_object(image.object_key)
        except ObjectNotFound:
            return Response(status=status.HTTP_404_NOT_FOUND)
        except StorageUnavailable:
            return Response({"detail": "이미지 저장소를 사용할 수 없습니다."}, status=503)

        def chunks():
            try:
                while chunk := body.read(64 * 1024):
                    yield chunk
            finally:
                body.close()

        response = StreamingHttpResponse(chunks(), content_type=image.content_type)
        response["Content-Length"] = image.size
        response["X-Content-Type-Options"] = "nosniff"
        response["Cache-Control"] = "no-store" if published else "private, no-store"
        return response

    @extend_schema(
        responses={
            204: OpenApiResponse(description="본문 없음"),
            401: OpenApiTypes.OBJECT,
            403: OpenApiTypes.OBJECT,
            404: OpenApiTypes.OBJECT,
            409: OpenApiTypes.OBJECT,
            503: OpenApiTypes.OBJECT,
        }
    )
    def delete(self, request, image_id):
        if not request.user.is_authenticated:
            return Response(status=status.HTTP_401_UNAUTHORIZED)
        try:
            with transaction.atomic():
                image = (
                    CommunityImage.objects.select_for_update(of=("self",))
                    .select_related("draft")
                    .filter(pk=image_id)
                    .first()
                )
                if image is None:
                    return Response(status=status.HTTP_404_NOT_FOUND)
                if image.owner_id != request.user.pk:
                    return Response(status=status.HTTP_403_FORBIDDEN)
                if _published(image):
                    return Response(
                        {"detail": "게시된 글에 연결된 이미지는 삭제할 수 없습니다."},
                        status=status.HTTP_409_CONFLICT,
                    )
                key = image.object_key
                image.delete()
                delete_object(key)
        except StorageUnavailable:
            return Response({"detail": "이미지 저장소를 사용할 수 없습니다."}, status=503)
        return Response(status=status.HTTP_204_NO_CONTENT)
