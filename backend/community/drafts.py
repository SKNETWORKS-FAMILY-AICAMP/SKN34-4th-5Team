from django.db import transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404
from drf_spectacular.utils import OpenApiResponse, OpenApiTypes, extend_schema, extend_schema_view
from pydantic import ValidationError as PydanticValidationError
from rest_framework import serializers, status
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .draft_schemas import DraftInput, DraftOutput, DraftPatch
from .models import CommunityDraft, CommunityImage
from .pagination import PublicPageNumberPagination


def validated(schema, data):
    try:
        return schema.model_validate(data)
    except PydanticValidationError as exc:
        errors = {}
        for error in exc.errors(include_url=False):
            field = ".".join(map(str, error["loc"])) or "nonFieldErrors"
            errors.setdefault(field, []).append(error["msg"])
        raise ValidationError(errors) from None


def draft_data(draft):
    return DraftOutput.model_validate(draft).model_dump(by_alias=True, mode="json")


def owner_drafts(user):
    return CommunityDraft.objects.filter(owner=user, published_post__isnull=True)


class DraftPagination(PublicPageNumberPagination):
    page_size = 20
    page_size_query_param = "page_size"
    max_page_size = 100


class DraftOutputSchema(serializers.Serializer):
    isTrade = serializers.BooleanField()
    isAdvertisement = serializers.BooleanField()
    id = serializers.UUIDField()
    board = serializers.ChoiceField(choices=("free", "teams"))
    teamCode = serializers.CharField(allow_blank=True)
    category = serializers.CharField(allow_blank=True)
    title = serializers.CharField(allow_blank=True)
    content = serializers.CharField(allow_blank=True)
    revision = serializers.IntegerField(min_value=1)
    createdAt = serializers.DateTimeField()
    updatedAt = serializers.DateTimeField()
    imageIds = serializers.ListField(child=serializers.UUIDField())


class DraftPageSchema(serializers.Serializer):
    count = serializers.IntegerField(min_value=0)
    next = serializers.CharField(allow_null=True)
    previous = serializers.CharField(allow_null=True)
    results = DraftOutputSchema(many=True)


class DraftPatchSchema(serializers.Serializer):
    isTrade = serializers.BooleanField(required=False)
    isAdvertisement = serializers.BooleanField(required=False)
    revision = serializers.IntegerField(min_value=1)
    board = serializers.ChoiceField(choices=("free", "teams"), required=False)
    teamCode = serializers.CharField(max_length=2, allow_blank=True, required=False)
    category = serializers.CharField(max_length=20, allow_blank=True, required=False)
    title = serializers.CharField(max_length=200, allow_blank=True, required=False)
    content = serializers.CharField(max_length=20000, allow_blank=True, required=False)
    imageIds = serializers.ListField(child=serializers.UUIDField(), max_length=10, required=False)


@extend_schema_view(
    get=extend_schema(operation_id="community_drafts_list", responses={200: DraftPageSchema, 401: OpenApiTypes.OBJECT}),
    post=extend_schema(operation_id="community_drafts_create", request=DraftInput, responses={201: DraftOutputSchema, 400: OpenApiTypes.OBJECT, 401: OpenApiTypes.OBJECT}),
)
class CommunityDraftListCreateView(APIView):
    permission_classes = (IsAuthenticated,)
    http_method_names = ("get", "post", "head", "options")

    def get(self, request):
        paginator = DraftPagination()
        page = paginator.paginate_queryset(owner_drafts(request.user).order_by("-updated_at", "-id"), request, view=self)
        return paginator.get_paginated_response([draft_data(draft) for draft in page])

    def post(self, request):
        values = validated(DraftInput, request.data).model_dump()
        draft = CommunityDraft.objects.create(owner=request.user, **values)
        return Response(draft_data(draft), status=status.HTTP_201_CREATED)


@extend_schema_view(
    get=extend_schema(operation_id="community_drafts_retrieve", responses={200: DraftOutputSchema, 401: OpenApiTypes.OBJECT, 404: OpenApiTypes.OBJECT}),
    patch=extend_schema(operation_id="community_drafts_partial_update", request=DraftPatchSchema, responses={200: DraftOutputSchema, 400: OpenApiTypes.OBJECT, 401: OpenApiTypes.OBJECT, 404: OpenApiTypes.OBJECT, 409: OpenApiTypes.OBJECT}),
    delete=extend_schema(operation_id="community_drafts_destroy", responses={204: OpenApiResponse(description="본문 없음"), 401: OpenApiTypes.OBJECT, 404: OpenApiTypes.OBJECT}),
)
class CommunityDraftDetailView(APIView):
    permission_classes = (IsAuthenticated,)
    http_method_names = ("get", "patch", "delete", "head", "options")

    @staticmethod
    def get_object(user, draft_id, *, for_update=False):
        queryset = owner_drafts(user)
        return get_object_or_404(queryset.select_for_update() if for_update else queryset, pk=draft_id)

    def get(self, request, draft_id):
        return Response(draft_data(self.get_object(request.user, draft_id)))

    def patch(self, request, draft_id):
        patch = validated(DraftPatch, request.data)
        with transaction.atomic():
            draft = self.get_object(request.user, draft_id, for_update=True)
            if draft.revision != patch.revision:
                return Response(
                    {"revision": ["임시저장본이 다른 요청에서 수정되었습니다."]},
                    status=status.HTTP_409_CONFLICT,
                )
            changes = patch.model_dump(exclude={"revision"}, exclude_unset=True)
            values = validated(
                DraftInput,
                {
                    "board": changes.get("board", draft.board),
                    "team_code": changes.get("team_code", draft.team_code),
                    "category": changes.get("category", draft.category),
                    "title": changes.get("title", draft.title),
                    "content": changes.get("content", draft.content),
                    "is_trade": changes.get('is_trade', draft.is_trade),
                    "is_advertisement": changes.get('is_advertisement', draft.is_advertisement),
                },
            ).model_dump()
            if patch.image_ids is not None:
                image_ids = patch.image_ids
                locked_images = list(
                    CommunityImage.objects.select_for_update(of=("self",)).filter(
                        Q(draft=draft) | Q(pk__in=image_ids)
                    )
                )
                targets = [image for image in locked_images if image.pk in image_ids]
                if len(targets) != len(image_ids) or any(
                    image.owner_id != request.user.id or image.post_id or image.draft_id not in (None, draft.pk)
                    for image in targets
                ):
                    raise ValidationError({"imageIds": ["본인의 미게시 이미지만 첨부할 수 있습니다."]})
                CommunityImage.objects.filter(draft=draft).exclude(pk__in=image_ids).update(draft=None)
                CommunityImage.objects.filter(pk__in=image_ids).update(draft=draft)
            for field, value in values.items():
                setattr(draft, field, value)
            draft.revision += 1
            draft.save(update_fields=(*values, "revision", "updated_at"))
        return Response(draft_data(draft))

    def delete(self, request, draft_id):
        with transaction.atomic():
            draft = self.get_object(request.user, draft_id, for_update=True)
            list(CommunityImage.objects.select_for_update(of=("self",)).filter(draft=draft))
            draft.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)
