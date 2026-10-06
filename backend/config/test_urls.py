import uuid

from django.test import SimpleTestCase
from django.urls import resolve

from baseball.views import RESOURCE_VIEWSETS
from community.views import CommunityPostListCreateView
from llm.views import ChatFinalizeView, GuestChatView
from travel.views import CourseListCreateView


class RootUrlResolutionTests(SimpleTestCase):
    def test_feature_routes_resolve_to_expected_views(self):
        turn_id = uuid.uuid4()
        cases = (
            ("/courses/", CourseListCreateView),
            ("/community/posts/", CommunityPostListCreateView),
            ("/baseball/manage/teams/", RESOURCE_VIEWSETS["teams"]),
            (f"/chat/turns/{turn_id}/finalize/", ChatFinalizeView),
            ("/chat/guest/", GuestChatView),
        )

        for path, view_class in cases:
            with self.subTest(path=path):
                func = resolve(path).func
                self.assertIs(getattr(func, "view_class", getattr(func, "cls", None)), view_class)
