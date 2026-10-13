from django.db.models import F
from .models import CommunityPost, CommunityComment


def public_posts(qs=None):
    return (CommunityPost.objects.all() if qs is None else qs).filter(
        is_hidden=False, approved_revision=F('content_revision'))


def public_comments(qs=None):
    return (CommunityComment.objects.all() if qs is None else qs).filter(
        approved_revision=F('content_revision'),
        post__is_hidden=False,
        post__approved_revision=F('post__content_revision'))


def approved(obj):
    return obj.approved_revision == obj.content_revision


def post_public(obj):
    return approved(obj) and not obj.is_hidden
