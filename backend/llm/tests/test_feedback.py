import uuid

from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.test import TestCase, override_settings
from langchain_core.messages import AIMessage, HumanMessage
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import AccessToken

from llm.models import AnswerFeedback, ChatSession
from llm.service.chat_thread import ChatThread


@override_settings(ROOT_URLCONF="config.urls")
class FeedbackTests(TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        ChatThread.setup()

    def setUp(self):
        User = get_user_model()
        self.owner = User.objects.create_user(username="feedback-owner", password="test-only-password")
        self.other = User.objects.create_user(username="feedback-other", password="test-only-password")
        self.admin = User.objects.create_user(username="feedback-admin", password="test-only-password", is_superuser=True)
        self.staff = User.objects.create_user(username="feedback-staff", password="test-only-password", is_staff=True)
        self.session = ChatSession.objects.create(user=self.owner)
        self.thread = ChatThread(self.session.id)
        self.addCleanup(self.thread.delete)
        self.human = HumanMessage("server question", id=str(uuid.uuid4()))
        self.answer = AIMessage("server answer", id=str(uuid.uuid4()), response_metadata={"chain_version": "v2"})
        self.thread.update([self.human, self.answer], {self.human.id: {"status": "completed", "answer_id": self.answer.id}})
        self.answer_no = self.thread.wire[self.answer.id]["id"]
        self.path = f"/api/v2/chat/sessions/{self.session.id}/feedback/"
        self.client = self.member(self.owner)

    def member(self, user):
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {AccessToken.for_user(user)}")
        return client

    def vote(self, rating="up", **extra):
        return self.client.put(self.path, {"message_id": self.answer_no, "rating": rating, **extra}, format="json")

    def test_create_change_cancel_reload_and_server_snapshots(self):
        self.assertEqual(self.vote().status_code, 200)
        self.assertEqual(self.vote().status_code, 200)
        self.assertEqual(AnswerFeedback.objects.count(), 1)
        row = AnswerFeedback.objects.get()
        self.assertEqual((row.answer_id, row.question, row.answer, row.metadata), (self.answer.id, "server question", "server answer", {"version": "v2"}))
        response = self.vote("down", reason="incorrect", comment="수정 필요")
        self.assertEqual(response.data["feedback"], {"rating": "down", "reason": "incorrect", "comment": "수정 필요"})
        history = self.client.get(f"/api/v2/chat/sessions/{self.session.id}/messages/")
        self.assertEqual(history.status_code, 200)
        self.assertEqual(history.data[-1]["feedback"], response.data["feedback"])
        self.assertNotIn("metadata", history.data[-1])
        self.assertEqual(self.vote(None).data, {"feedback": None})
        self.assertEqual(self.vote(None).status_code, 200)
        self.assertFalse(AnswerFeedback.objects.exists())

    def test_rating_updates_preserve_original_snapshots(self):
        self.vote()
        before = AnswerFeedback.objects.get()
        self.thread.update([
            HumanMessage("changed question", id=self.human.id),
            AIMessage("changed answer", id=self.answer.id, response_metadata={"chain_version": "v1", "model": "changed"}),
        ], {})
        self.assertEqual(self.vote("down", reason="incorrect", comment="updated").status_code, 200)
        after = AnswerFeedback.objects.get()
        self.assertEqual((after.question, after.answer, after.metadata, after.message_id, after.created_at),
                         (before.question, before.answer, before.metadata, before.message_id, before.created_at))
        self.assertEqual((after.rating, after.reason, after.comment), ("down", "incorrect", "updated"))

    def test_ownership_and_guest_capability(self):
        for client in (self.member(self.other), APIClient(), self.member(self.admin)):
            self.assertEqual(client.put(self.path, {"message_id": self.answer_no, "rating": "up"}, format="json").status_code, 404)
        guest_id = uuid.uuid4()
        guest_session = ChatSession.objects.create(guest=guest_id)
        thread = ChatThread(guest_session.id)
        self.addCleanup(thread.delete)
        thread.update([self.human, self.answer], {self.human.id: {"status": "completed", "answer_id": self.answer.id}})
        path = f"/api/v1/chat/sessions/{guest_session.id}/feedback/"
        guest = APIClient()
        guest.cookies["guest_id"] = str(guest_id)
        self.assertEqual(guest.put(path, {"message_id": 2, "rating": "up"}, format="json").status_code, 200)
        self.assertEqual(guest.get(f"/api/v1/chat/sessions/{guest_session.id}/messages/").data[-1]["feedback"]["rating"], "up")
        guest.cookies["guest_id"] = str(uuid.uuid4())
        self.assertEqual(guest.put(path, {"message_id": 2, "rating": "up"}, format="json").status_code, 404)
        guest.cookies["guest_id"] = "invalid"
        self.assertEqual(guest.put(path, {"message_id": 2, "rating": "up"}, format="json").status_code, 404)
        member = self.member(self.owner)
        member.cookies["guest_id"] = str(guest_id)
        self.assertEqual(member.put(path, {"message_id": 2, "rating": "up"}, format="json").status_code, 404)

    def test_validation_and_nonfinal_rejection(self):
        for body in ({"message_id": True, "rating": "up"}, {"message_id": 0, "rating": "up"}, {"message_id": 2, "rating": "bad"}, {"message_id": 2, "rating": "down", "reason": "bad"}, {"message_id": 2, "rating": "down", "comment": "x" * 1001}, {"message_id": 2, "rating": "up", "comment": "not allowed"}, {"message_id": 2, "rating": "down", "comment": 123}):
            self.assertEqual(self.client.put(self.path, body, format="json").status_code, 400, body)
        self.assertEqual(self.client.put(self.path, {"message_id": 1, "rating": "up"}, format="json").status_code, 404)
        self.assertEqual(self.client.put(self.path, {"message_id": 999, "rating": "up"}, format="json").status_code, 404)
        for status in ("pending", "failed", "cancelled"):
            self.thread.update([], {self.human.id: {"status": status, "answer_id": self.answer.id}})
            self.assertEqual(self.vote().status_code, 404)
        planner = AIMessage("not a final answer", id=str(uuid.uuid4()))
        self.thread.update([planner], {self.human.id: {"status": "completed", "answer_id": self.answer.id}})
        self.assertEqual(self.client.put(self.path, {"message_id": self.thread.wire[planner.id]["id"], "rating": "up"}, format="json").status_code, 404)

    def test_stale_answer_snapshot_survives_edit_but_session_deletion_cascades(self):
        self.vote()
        self.thread.edit(1, "edited question")
        self.assertEqual(self.vote().status_code, 404)
        row = AnswerFeedback.objects.get()
        self.assertEqual(row.question, "server question")
        self.assertEqual(self.client.put(self.path, {"message_id": self.answer.id, "rating": "down"}, format="json").status_code, 404)
        with self.captureOnCommitCallbacks(execute=True):
            self.session.delete()
        self.assertFalse(AnswerFeedback.objects.exists())

    def test_account_deletion_cascades_and_unique_constraint(self):
        self.vote()
        row = AnswerFeedback.objects.get()
        with self.assertRaises(IntegrityError), transaction.atomic():
            AnswerFeedback.objects.create(session=self.session, answer_id=row.answer_id, message_id=2, rating="up", question="q", answer="a")
        with self.captureOnCommitCallbacks(execute=True):
            self.owner.delete()
        self.assertFalse(AnswerFeedback.objects.exists())

    def test_admin_enforces_superuser_filters_pagination_detail(self):
        self.vote("down", reason="other")
        path = "/api/v2/chat/admin/feedback/"
        for client in (self.client, self.member(self.staff), APIClient()):
            self.assertIn(client.get(path).status_code, (401, 403))
            self.assertIn(client.get(f"{path}{AnswerFeedback.objects.get().id}/").status_code, (401, 403))
        admin = self.member(self.admin)
        response = admin.get(path, {"rating": "down", "reason": "other"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["count"], 1)
        self.assertEqual(admin.get(path, {"rating": "up"}).data["count"], 0)
        self.assertEqual(admin.get(path, {"rating": "invalid"}).status_code, 400)
        row_id = response.data["results"][0]["id"]
        self.assertEqual(admin.get(f"{path}{row_id}/").data["answer"], "server answer")
        AnswerFeedback.objects.bulk_create([AnswerFeedback(session=self.session, answer_id=str(uuid.uuid4()), message_id=i + 3, rating="up", question="q", answer="a") for i in range(21)])
        self.assertEqual(len(admin.get(path).data["results"]), 20)
        self.assertEqual(len(admin.get(path, {"page": 2}).data["results"]), 2)
        self.assertEqual(admin.get(path, {"page": 3}).status_code, 404)
