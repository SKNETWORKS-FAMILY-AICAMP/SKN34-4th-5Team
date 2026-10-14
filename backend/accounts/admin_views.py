"""Member administration. Only a superuser may change staff membership."""
import json

from django.contrib.auth import get_user_model
from django.contrib.admin.models import LogEntry, CHANGE
from django.contrib.contenttypes.models import ContentType
from django.db import transaction
from django.db.models import Count, Q
from rest_framework import generics, serializers
from rest_framework.pagination import PageNumberPagination
from rest_framework.permissions import BasePermission
from rest_framework.response import Response
from rest_framework.exceptions import PermissionDenied
from rest_framework.views import APIView
from drf_spectacular.utils import OpenApiParameter, extend_schema


class StaffOnly(BasePermission):
    def has_permission(self, request, view):
        return bool(request.user.is_authenticated and request.user.is_active and request.user.is_staff)


class MasterOnly(StaffOnly):
    def has_permission(self, request, view):
        return super().has_permission(request, view) and request.user.is_superuser


class AdminMemberSerializer(serializers.ModelSerializer):
    report_count = serializers.IntegerField(read_only=True)

    class Meta:
        model = get_user_model()
        fields = ("id", "username", "is_active", "is_staff", "is_superuser", "date_joined", "report_count")
        read_only_fields = fields


class MemberPages(PageNumberPagination):
    page_size = 20


class MemberList(generics.ListAPIView):
    permission_classes = (StaffOnly,)
    serializer_class = AdminMemberSerializer
    pagination_class = MemberPages

    @extend_schema(parameters=[OpenApiParameter("filter", str, enum=["reported_first", "reported", "newest"]), OpenApiParameter("q", str)])
    def get(self, request, *args, **kwargs):
        return super().get(request, *args, **kwargs)

    def get_queryset(self):
        selection = self.request.query_params.get("filter", "reported_first")
        if selection not in {"reported_first", "reported", "newest"}:
            raise serializers.ValidationError({"filter": "지원하지 않는 회원 필터입니다."})
        users = get_user_model().objects.annotate(report_count=Count("community_posts__reports", distinct=True))
        if selection == "reported":
            users = users.filter(report_count__gt=0)
        users = users.order_by("-date_joined", "-id") if selection == "newest" else users.order_by("-report_count", "id")
        query = self.request.query_params.get("q", "").strip()[:150]
        if query:
            condition = Q(username__icontains=query)
            if query.isascii() and query.isdigit() and len(query) < 19:
                condition |= Q(pk=int(query))
            users = users.filter(condition)
        return users


class AdminRoleUpdateSerializer(serializers.Serializer):
    is_staff = serializers.BooleanField()

    @staticmethod
    def parse(data):
        if not isinstance(data, dict) or set(data) != {"is_staff"} or type(data.get("is_staff")) is not bool:
            raise serializers.ValidationError("is_staff에 true 또는 false를 지정해 주세요.")
        return data["is_staff"]


class MemberRole(APIView):
    permission_classes = (MasterOnly,)

    @transaction.atomic
    @extend_schema(
        request={"application/json": {
            "type": "object", "additionalProperties": False,
            "required": ["is_staff"], "properties": {"is_staff": {"type": "boolean"}},
        }},
        responses=AdminMemberSerializer,
    )
    def patch(self, request, pk):
        after = AdminRoleUpdateSerializer.parse(request.data)
        member = generics.get_object_or_404(get_user_model().objects.select_for_update(), pk=pk)
        if member.pk == request.user.pk or member.is_superuser:
            raise PermissionDenied("마스터 관리자와 본인 계정의 권한은 변경할 수 없습니다.")
        if not member.is_active:
            raise serializers.ValidationError("비활성 계정에는 권한을 변경할 수 없습니다.")
        before = member.is_staff
        if before != after:
            member.is_staff = after
            member.save(update_fields=["is_staff"])
            LogEntry.objects.create(
                user_id=request.user.pk,
                content_type=ContentType.objects.get_for_model(member),
                object_id=str(member.pk), object_repr=member.get_username(), action_flag=CHANGE,
                change_message=json.dumps({"field": "is_staff", "before": before, "after": after}),
            )
        member.report_count = member.community_posts.aggregate(total=Count("reports", distinct=True))["total"]
        return Response(AdminMemberSerializer(member).data)
