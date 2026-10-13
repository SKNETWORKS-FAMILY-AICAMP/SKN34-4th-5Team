from django.db.models import F, Q
from django.shortcuts import get_object_or_404
from drf_spectacular.utils import extend_schema
from rest_framework import generics, serializers
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.admin_views import StaffOnly
from .admin_views import AdminPages
from .models import CommunityPost, CommunityComment, ModerationJob
from .serializers import CommunityPostSerializer, CommunityCommentSerializer
from .moderation_service import review_job, retry_job


class PrivateResponseMixin:
    def finalize_response(self, request, response, *args, **kwargs):
        response = super().finalize_response(request, response, *args, **kwargs)
        response['Cache-Control'] = 'private, no-store'
        return response


class OwnPostList(PrivateResponseMixin, generics.ListAPIView):
    permission_classes = (IsAuthenticated,)
    serializer_class = CommunityPostSerializer
    pagination_class = None

    def get_queryset(self):
        return CommunityPost.objects.filter(owner=self.request.user).order_by('-created_at')


class OwnPostDetail(PrivateResponseMixin, generics.RetrieveAPIView):
    permission_classes = (IsAuthenticated,)
    serializer_class = CommunityPostSerializer
    lookup_field = 'source_id'

    def get_queryset(self):
        return CommunityPost.objects.filter(owner=self.request.user)


class OwnCommentList(PrivateResponseMixin, generics.ListAPIView):
    permission_classes = (IsAuthenticated,)
    serializer_class = CommunityCommentSerializer
    pagination_class = None

    def get_queryset(self):
        return CommunityComment.objects.filter(author=self.request.user).select_related('author').order_by('-created_at')


class JobSerializer(serializers.ModelSerializer):
    postId = serializers.CharField(source='post.source_id', read_only=True, default=None)
    commentId = serializers.IntegerField(source='comment_id', read_only=True, allow_null=True)
    manualHidden = serializers.SerializerMethodField()

    def get_manualHidden(self, obj) -> bool:
        return (obj.post if obj.post_id else obj.comment.post).is_hidden

    class Meta:
        model = ModerationJob
        fields = ('id', 'postId', 'commentId', 'revision', 'review_revision', 'state',
                  'payload', 'findings', 'evidence', 'error_code', 'attempts', 'manualHidden', 'created_at')
        read_only_fields = fields


class ReviewInput(serializers.Serializer):
    revision = serializers.IntegerField(min_value=1)
    review_revision = serializers.IntegerField(min_value=0)
    finding_ids = serializers.ListField(child=serializers.CharField(max_length=64), allow_empty=False, max_length=100)
    action = serializers.ChoiceField(choices=('dismiss', 'confirm'))
    note = serializers.CharField(max_length=500, allow_blank=False)


class ModerationList(PrivateResponseMixin, generics.ListAPIView):
    permission_classes = (StaffOnly,)
    serializer_class = JobSerializer
    pagination_class = AdminPages

    def get_queryset(self):
        return ModerationJob.objects.select_related('post', 'comment__post').filter(
            Q(revision=F('post__content_revision')) | Q(revision=F('comment__content_revision'))
        ).order_by('-created_at')


class ModerationReviewView(PrivateResponseMixin, APIView):
    permission_classes = (StaffOnly,)

    @extend_schema(request=ReviewInput, responses=JobSerializer)
    def post(self, request, job_id):
        get_object_or_404(ModerationJob, pk=job_id)
        data = ReviewInput(data=request.data)
        data.is_valid(raise_exception=True)
        job = review_job(job_id, request.user, **data.validated_data)
        return Response(JobSerializer(job).data)


class ModerationRetryView(PrivateResponseMixin, APIView):
    permission_classes = (StaffOnly,)

    @extend_schema(request=None, responses={202: JobSerializer})
    def post(self, request, job_id):
        get_object_or_404(ModerationJob, pk=job_id)
        return Response(JobSerializer(retry_job(job_id, request.user)).data, status=202)
