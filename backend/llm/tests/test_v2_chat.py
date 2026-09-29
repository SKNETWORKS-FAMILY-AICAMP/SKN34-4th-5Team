"""backend/llm/service/chat.py + views(sesstion/message) 채팅 스트리밍 테스트.

체인은 전부 가짜(FakeChain)로 바꿔서 실제 LLM/OpenAI 호출 없이 돈다. 대화 원본은
LangGraph checkpoint(PostgresSaver) 테이블에 별도 연결로 커밋되므로 docker 컨테이너 안의 PostgreSQL 에서
python manage.py test 로 실행한다. thread_id 는 매번 새 세션 uuid 라 테스트끼리 섞이지 않고,
남은 행은 테스트 DB 와 함께 사라진다.
"""
import json
import os
import sys
import types
import uuid
from unittest.mock import patch as mock_patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from langchain_core.messages import AIMessage, HumanMessage
from rest_framework.test import APIClient

from llm.models import ChatSession
from llm.serializer.message import ANSWER_TEXT_EVENT, GENERIC_ERROR_MESSAGE
from llm.service import chat as chat_service
from llm.service.chat import ChatThread

User = get_user_model()


class FakeChain:
    """v2 chain.stream(inputs) -> Iterator[str] / v1 chain.astream_events(inputs) 를 흉내내는 가짜 체인."""

    def __init__(self, chunks=("안녕", "하세요")):
        self.chunks = chunks
        self.received_inputs = None

    def stream(self, inputs):
        self.received_inputs = inputs
        for chunk in self.chunks:
            yield chunk

    async def astream_events(self, inputs, version):
        # v1 경로: 모델 없는 답과 같은 공개 답변 이벤트로 흘리고 run["answer"] 에 전문을 남긴다.
        self.received_inputs = inputs
        for chunk in self.chunks:
            yield {"event": "on_custom_event", "name": ANSWER_TEXT_EVENT, "data": chunk, "run_id": "fake"}
        inputs["run"]["answer"] = "".join(self.chunks)


class FailingChain:
    def stream(self, inputs):
        self.received_inputs = inputs
        raise RuntimeError("provider exploded")
        yield  # pragma: no cover - generator 표시용


def _read_sse_body(body):
    """SSE 와이어 포맷 문자열을 (event, data) 리스트로 파싱한다."""
    frames = []
    for block in body.split("\n\n"):
        block = block.strip()
        if not block:
            continue
        event_line, data_line = block.split("\n")
        event = event_line.removeprefix("event: ")
        data = json.loads(data_line.removeprefix("data: "))
        frames.append((event, data))
    return frames


class CheckpointTestCase(TestCase):
    """checkpoint 테이블은 migrate 가 아니라 setup_chat_checkpoints 로 만든다 -- 테스트 DB 에도 한 번."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        ChatThread.setup()


def seed(session, *pairs):
    """(질문, 답변) 완료 턴들을 최신 스냅샷으로 저장하고 [Human, AI, Human, AI, ...] 를 돌려준다."""
    messages, turns = [], {}
    for question, answer in pairs:
        human, ai = HumanMessage(question, id=str(uuid.uuid4())), AIMessage(answer, id=str(uuid.uuid4()))
        messages += [human, ai]
        turns[human.id] = {"status": "completed", "answer_id": ai.id}
    ChatThread(session.id).update(messages, turns)
    return messages


def snapshot(session):
    """(messages, turns) -- 저장된 적 없으면 ([], {})."""
    return ChatThread(session.id).state()


def history(session):
    return chat_service.project_history(*snapshot(session))


def guest_request(session):
    return types.SimpleNamespace(
        user=types.SimpleNamespace(is_authenticated=False),
        COOKIES={"guest_id": str(session.guest)},
    )


class GetChainVersionTest(TestCase):
    """llm.v2.agent.chain 은 langchain_typesafe(미설치) 를 무겁게 import 하므로, 실제 모듈
    대신 sys.modules 에 가짜 llm.v2.agent.chain / llm.v1.rag.pipeline 을 심어 get_chain()의
    분기(버전 문자열 -> 어느 모듈의 무엇을 리턴하는지)만 검증한다. 실제 v1/v2 체인
    내부 동작은 각각 llm/v1/rag 쪽 테스트와 llm/tests/test_agent_chains.py 가 맡는다.
    """

    def setUp(self):
        self.fake_v2_module = types.ModuleType("llm.v2.agent.chain")
        self.fake_v2_module.chain = object()

        self.fake_v1_module = types.ModuleType("llm.v1.rag.pipeline")
        self.fake_v1_module.chat_chain = lambda: "fake-v1-chain"

        patcher = mock_patch.dict(sys.modules, {
            "llm.v2.agent.chain": self.fake_v2_module,
            "llm.v1.rag.pipeline": self.fake_v1_module,
        })
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_v1_calls_chat_chain(self):
        self.assertEqual(chat_service.get_chain("v1"), "fake-v1-chain")

    def test_v2_returns_agent_chain(self):
        self.assertIs(chat_service.get_chain("v2"), self.fake_v2_module.chain)

    def test_defaults_to_v2_when_env_unset(self):
        with mock_patch.dict(os.environ, {}, clear=False):
            os.environ.pop("LLM_CHAIN_VERSION", None)
            self.assertIs(chat_service.get_chain(), self.fake_v2_module.chain)

    def test_env_var_selects_v1(self):
        with mock_patch.dict(os.environ, {"LLM_CHAIN_VERSION": "v1"}):
            self.assertEqual(chat_service.get_chain(), "fake-v1-chain")


class ResolveVersionTest(TestCase):
    """URL 로 넘어온 version 이 env 보다 우선한다는 선택 규칙 (resolve_version) 회귀 테스트.
    llm/urls.py 를 두 api 버전이 공통 include 하면서 실제 분기가 여기로 옮겨왔다."""

    def test_explicit_version_overrides_env(self):
        with mock_patch.dict(os.environ, {"LLM_CHAIN_VERSION": "v2"}):
            self.assertEqual(chat_service.resolve_version("v1"), "v1")

    def test_none_falls_back_to_env(self):
        with mock_patch.dict(os.environ, {"LLM_CHAIN_VERSION": "v1"}):
            self.assertEqual(chat_service.resolve_version(None), "v1")

    def test_unsupported_version_raises_without_silent_fallback(self):
        with mock_patch.dict(os.environ, {"LLM_CHAIN_VERSION": "v2"}):
            with self.assertRaises(ValueError):
                chat_service.resolve_version("v3")


class SendMessageTest(CheckpointTestCase):
    def setUp(self):
        self.session = ChatSession.objects.create(guest="11111111-1111-1111-1111-111111111111")

    def _patched_chain(self, chain):
        return mock_patch("llm.service.chat.get_chain", return_value=chain)

    def test_sse_frame_order_and_persistence(self):
        fake = FakeChain(chunks=("안", "녕"))
        with self._patched_chain(fake):
            frames = list(chat_service.send_message(self.session, "안녕?"))

        events = [event for event, _ in frames]
        self.assertEqual(events, ["delta", "delta", "done"])
        self.assertEqual(frames[0][1], {"text": "안"})
        self.assertEqual(frames[1][1], {"text": "녕"})
        self.assertEqual(frames[2][1]["assistant_message"], "안녕")
        self.assertEqual(frames[2][1]["tools"], [])

        messages, turns = snapshot(self.session)
        human, answer = messages
        self.assertIsInstance(human, HumanMessage)
        self.assertIsInstance(answer, AIMessage)
        self.assertEqual((human.content, answer.content), ("안녕?", "안녕"))
        self.assertEqual(turns, {human.id: {"status": "completed", "answer_id": answer.id}})
        self.assertEqual(frames[2][1]["message_id"], "2")  # 공개 wire: 옛 정수 id 의 숫자 문자열 (thread 안 2번째 메시지)

    def test_history_passed_to_chain_excludes_current_question(self):
        seed(self.session, ("이전 질문", "이전 답변"))
        fake = FakeChain()
        with self._patched_chain(fake):
            list(chat_service.send_message(self.session, "새 질문"))  # 제너레이터는 실제로 순회해야 chain.stream() 이 돈다

        history_in = fake.received_inputs["chat_history"]
        self.assertEqual([type(m) for m in history_in], [HumanMessage, AIMessage])
        self.assertEqual([m.content for m in history_in], ["이전 질문", "이전 답변"])
        self.assertEqual(fake.received_inputs["question"], "새 질문")

    def test_history_passed_to_chain_skips_unfinished_turns(self):
        """실패/취소 턴의 질문은 모델 입력에 다시 넣지 않는다 (완료된 질문/답변만)."""
        with self._patched_chain(FailingChain()):
            list(chat_service.send_message(self.session, "실패한 질문"))
        fake = FakeChain()
        with self._patched_chain(fake):
            list(chat_service.send_message(self.session, "새 질문"))
        self.assertEqual(fake.received_inputs["chat_history"], [])

    def test_send_message_rejects_unsupported_version_before_insert(self):
        """send_message(version="v3") 는 chain 호출/메시지 저장 전에 막힌다 (조용한 v2 폴백 없음)."""
        fake = FakeChain(chunks=("안",))
        with self._patched_chain(fake):
            with self.assertRaises(ValueError):
                chat_service.send_message(self.session, "질문", version="v3")
        self.assertEqual(snapshot(self.session), ([], {}))

    def test_message_update_rejects_unsupported_version_before_truncate(self):
        """message_update(version="v3") 는 절단 전에 막혀서 대상 메시지가 그대로 남아야 한다."""
        target = seed(self.session, ("수정 대상", "답변"))[0]
        before = snapshot(self.session)
        fake = FakeChain(chunks=("안",))
        with self._patched_chain(fake):
            with self.assertRaises(ValueError):
                chat_service.message_update(guest_request(self.session), self.session.id, target.id, "수정된 질문", version="v3")
        self.assertEqual(snapshot(self.session), before)
        self.assertIsNone(fake.received_inputs)

    def test_failure_path_emits_error_frame_and_marks_failed(self):
        failing = FailingChain()
        with self._patched_chain(failing):
            frames = list(chat_service.send_message(self.session, "터질 질문"))

        self.assertEqual(frames, [("error", {"detail": GENERIC_ERROR_MESSAGE})])
        self.assertNotIn("provider exploded", frames[0][1]["detail"])

        messages, turns = snapshot(self.session)
        self.assertEqual([m.content for m in messages], ["터질 질문"])  # 가짜 AI 답변을 만들지 않는다
        self.assertEqual(turns[messages[0].id], {"status": "failed", "answer_id": None})


class ChatMessageViewSSEWireFormatTest(CheckpointTestCase):
    """POST /messages/ 가 실제로 text/event-stream 바이트를 그대로 내보내는지 HTTP 경로로 확인."""

    def setUp(self):
        self.client_a = APIClient()
        self.client_a.cookies["guest_id"] = "cccccccc-cccc-cccc-cccc-cccccccccccc"
        self.session = ChatSession.objects.create(guest="cccccccc-cccc-cccc-cccc-cccccccccccc")

    def test_post_message_streams_sse_wire_format(self):
        fake = FakeChain(chunks=("안", "녕"))
        with mock_patch("llm.service.chat.get_chain", return_value=fake):
            response = self.client_a.post(
                f"/api/v2/chat/sessions/{self.session.id}/messages/",
                {"content": "안녕?"},
                format="json",
                HTTP_ACCEPT="text/event-stream",
            )
            body = b"".join(response.streaming_content).decode("utf-8")

        self.assertEqual(response["Content-Type"], "text/event-stream")
        self.assertEqual(response["Cache-Control"], "no-cache")
        self.assertEqual(response["X-Accel-Buffering"], "no")

        frames = _read_sse_body(body)
        events = [event for event, _ in frames]
        self.assertEqual(events, ["delta", "delta", "done"])
        self.assertEqual(frames[0][1], {"text": "안"})
        self.assertEqual(frames[1][1], {"text": "녕"})
        self.assertEqual(frames[2][1]["assistant_message"], "안녕")


class SessionOwnershipViewTest(CheckpointTestCase):
    def setUp(self):
        self.client_a = APIClient()
        self.client_a.cookies["guest_id"] = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
        self.session_a = ChatSession.objects.create(guest="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")

        self.client_b = APIClient()
        self.client_b.cookies["guest_id"] = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"

        self.member = User.objects.create_user(username="member1", password="pw12345!")
        self.member_client = APIClient()
        self.member_client.force_authenticate(self.member)
        self.session_member = ChatSession.objects.create(user=self.member)

    def test_guest_cannot_access_other_guest_session(self):
        response = self.client_b.patch(
            f"/api/v2/chat/sessions/{self.session_a.id}/", {"title": "탈취 시도"}, format="json",
        )
        self.assertEqual(response.status_code, 404)

    def test_guest_cannot_access_member_session(self):
        response = self.client_b.patch(
            f"/api/v2/chat/sessions/{self.session_member.id}/", {"title": "탈취 시도"}, format="json",
        )
        self.assertEqual(response.status_code, 404)

    def test_guest_cannot_reach_others_messages(self):
        response = self.client_b.get(f"/api/v2/chat/sessions/{self.session_a.id}/messages/")
        self.assertEqual(response.status_code, 404)

    def test_guest_can_access_own_session_messages(self):
        response = self.client_a.get(f"/api/v2/chat/sessions/{self.session_a.id}/messages/")
        self.assertEqual(response.status_code, 200)

    def test_guest_without_cookie_gets_empty_list_not_others(self):
        anon = APIClient()
        response = anon.get("/api/v2/chat/sessions/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), [])

    def test_perform_create_for_member(self):
        response = self.member_client.post("/api/v2/chat/sessions/", {"title": "회원 세션"})
        self.assertEqual(response.status_code, 201)
        created = ChatSession.objects.get(id=response.json()["id"])
        self.assertEqual(created.user, self.member)
        self.assertIsNone(created.guest)

    def test_perform_create_for_guest_sets_cookie_and_guest_owner(self):
        anon = APIClient()
        response = anon.post("/api/v2/chat/sessions/", {"title": "비회원 세션"})
        self.assertEqual(response.status_code, 201)
        created = ChatSession.objects.get(id=response.json()["id"])
        self.assertIsNone(created.user)
        self.assertIsNotNone(created.guest)
        self.assertIn("guest_id", response.cookies)


class MessageContextValidationTest(CheckpointTestCase):
    """POST /messages/ 의 선택 사항 context (stadium/intent/origin) HTTP 계층 검증.

    실제 모델 호출 없이 FakeChain 이 받은 inputs["context"] 로 서비스까지 전달됐는지 확인한다.
    """

    def setUp(self):
        self.client_a = APIClient()
        self.client_a.cookies["guest_id"] = "dddddddd-dddd-dddd-dddd-dddddddddddd"
        self.session = ChatSession.objects.create(guest="dddddddd-dddd-dddd-dddd-dddddddddddd")

    def _post(self, payload):
        return self.client_a.post(
            f"/api/v2/chat/sessions/{self.session.id}/messages/",
            payload, format="json", HTTP_ACCEPT="text/event-stream",
        )

    def test_missing_context_is_backward_compatible(self):
        fake = FakeChain(chunks=("안",))
        with mock_patch("llm.service.chat.get_chain", return_value=fake):
            response = self._post({"content": "안녕"})
            self.assertEqual(response.status_code, 200)
            b"".join(response.streaming_content)  # patch 가 살아있는 동안 순회해야 스트림이 돈다
        self.assertNotIn("context", fake.received_inputs)

    def test_valid_context_reaches_chain_input(self):
        fake = FakeChain(chunks=("안",))
        context = {"stadium": "잠실야구장", "intent": "route", "origin": {"lat": 37.51, "lng": 127.07}}
        with mock_patch("llm.service.chat.get_chain", return_value=fake):
            response = self._post({"content": "코스 짜줘", "context": context})
            self.assertEqual(response.status_code, 200)
            b"".join(response.streaming_content)
        self.assertEqual(fake.received_inputs["context"]["stadium"], "잠실야구장")
        self.assertEqual(fake.received_inputs["context"]["intent"], "route")
        self.assertEqual(fake.received_inputs["context"]["origin"]["lat"], 37.51)
        self.assertEqual(fake.received_inputs["context"]["origin"]["lng"], 127.07)

    def test_invalid_intent_choice_rejected(self):
        response = self._post({"content": "질문", "context": {"intent": "not-a-real-intent"}})
        self.assertEqual(response.status_code, 400)

    def test_out_of_range_origin_coordinates_rejected(self):
        for bad in (200.0, -200.0):
            with self.subTest(lat=bad):
                response = self._post({"content": "질문", "context": {"origin": {"lat": bad, "lng": 127.0}}})
                self.assertEqual(response.status_code, 400)

    def test_nonfinite_origin_json_literals_rejected(self):
        # json.dumps 는 NaN/Infinity 를 못 만들어서(ValueError) DRF 클라이언트로는 못 보낸다.
        # 원시 JSON 텍스트로 그 리터럴을 직접 보내 FloatField 의 범위 검사가 걸리는지 확인한다.
        for literal in ("NaN", "Infinity", "-Infinity"):
            with self.subTest(literal=literal):
                body = '{"content": "질문", "context": {"origin": {"lat": %s, "lng": 127.0}}}' % literal
                response = self.client_a.post(
                    f"/api/v2/chat/sessions/{self.session.id}/messages/",
                    data=body, content_type="application/json", HTTP_ACCEPT="text/event-stream",
                )
                self.assertEqual(response.status_code, 400)

    def test_string_nan_lat_rejected(self):
        # DRF FloatField 는 문자열 "NaN"/"Infinity" 도 float() 변환만으로 통과시키고
        # min_value/max_value 비교는 NaN 과 항상 False 라 안 걸린다 (math.isfinite 로 직접 막는다).
        response = self._post({"content": "질문", "context": {"origin": {"lat": "NaN", "lng": 127.0}}})
        self.assertEqual(response.status_code, 400)

    def test_string_nan_lng_rejected(self):
        # lat 만이 아니라 lng 도 같은 문자열 "NaN" 우회 경로를 막는지 확인한다
        # (validate_lat/validate_lng 는 별도 메서드라 lng 쪽이 안 걸릴 수 있다).
        response = self._post({"content": "질문", "context": {"origin": {"lat": 37.5, "lng": "NaN"}}})
        self.assertEqual(response.status_code, 400)

    def test_missing_content_rejected(self):
        response = self._post({"context": {"stadium": "잠실야구장"}})
        self.assertEqual(response.status_code, 400)

    def test_blank_content_rejected(self):
        response = self._post({"content": ""})
        self.assertEqual(response.status_code, 400)

    def test_content_over_max_length_rejected(self):
        response = self._post({"content": "가" * 2201})
        self.assertEqual(response.status_code, 400)

    def test_content_max_length_boundary_accepted(self):
        fake = FakeChain(chunks=("답",))
        with mock_patch("llm.service.chat.get_chain", return_value=fake):
            response = self._post({"content": "가" * 2200})
            self.assertEqual(response.status_code, 200)
            b"".join(response.streaming_content)

    def test_non_string_content_rejected(self):
        response = self._post({"content": 12345})
        self.assertEqual(response.status_code, 400)

    def test_context_wrong_type_rejected(self):
        response = self._post({"content": "질문", "context": "not-an-object"})
        self.assertEqual(response.status_code, 400)

    def test_stadium_wrong_type_rejected(self):
        response = self._post({"content": "질문", "context": {"stadium": 123}})
        self.assertEqual(response.status_code, 400)

    def test_stadium_blank_rejected(self):
        response = self._post({"content": "질문", "context": {"stadium": ""}})
        self.assertEqual(response.status_code, 400)

    def test_stadium_over_max_length_rejected(self):
        response = self._post({"content": "질문", "context": {"stadium": "가" * 101}})
        self.assertEqual(response.status_code, 400)

    def test_origin_missing_lng_key_rejected(self):
        response = self._post({"content": "질문", "context": {"origin": {"lat": 37.5}}})
        self.assertEqual(response.status_code, 400)

    def test_origin_missing_lat_key_rejected(self):
        response = self._post({"content": "질문", "context": {"origin": {"lng": 127.0}}})
        self.assertEqual(response.status_code, 400)

    def test_origin_wrong_type_rejected(self):
        response = self._post({"content": "질문", "context": {"origin": "not-an-object"}})
        self.assertEqual(response.status_code, 400)

    def test_output_message_has_no_context_field(self):
        """GET 목록 항목은 공개 필드 {id, role, content, status, tools} 뿐이다 (context 없음).
        (옛 wire 호환 필드 sequence_no/created_at/updated_at 도 유지한다 -- 명시적 버전 없이 필드를 빼지 않는다.)"""
        seed(self.session, ("안녕", "반가워요"))
        response = self.client_a.get(f"/api/v2/chat/sessions/{self.session.id}/messages/")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(len(body), 2)
        for item in body:
            self.assertEqual(set(item), {"id", "sequence_no", "role", "content", "status", "tools", "created_at", "updated_at"})

    def test_put_updates_message_and_forwards_context_after_truncating_later_turns(self):
        """PUT 은 target 뒤를 RemoveMessage 로 지우고 target 을 같은 ID 의 새 질문으로 바꿔 다시 답한다.
        그 이전 턴은 보존되고, 새 context 는 그대로 체인 입력까지 전달돼야 한다."""
        earlier_q, earlier_a, target, stale_answer = seed(
            self.session, ("이전 질문", "이전 답변"), ("수정 대상 질문", "수정 전 답변"),
        )

        fake = FakeChain(chunks=("안",))
        context = {"stadium": "잠실야구장"}
        with mock_patch("llm.service.chat.get_chain", return_value=fake):
            response = self.client_a.put(
                f"/api/v2/chat/sessions/{self.session.id}/messages/",
                {"content": "수정된 질문", "message_id": target.id, "context": context},
                format="json", HTTP_ACCEPT="text/event-stream",
            )
            self.assertEqual(response.status_code, 200)
            frames = _read_sse_body(b"".join(response.streaming_content).decode("utf-8"))

        self.assertEqual([e for e, _ in frames], ["delta", "done"])
        items = history(self.session)
        self.assertEqual([i["content"] for i in items], ["이전 질문", "이전 답변", "수정된 질문", "안"])
        self.assertEqual([i["id"] for i in items[:2]], [earlier_q.id, earlier_a.id])  # 이전 턴 보존
        self.assertEqual(items[2]["id"], target.id)  # 수정 대상은 ID 를 유지한 채 내용만 바뀐다
        self.assertNotIn(stale_answer.id, {i["id"] for i in items})  # 이후 답변은 지워짐
        self.assertEqual([m.content for m in fake.received_inputs["chat_history"]], ["이전 질문", "이전 답변"])
        self.assertEqual(fake.received_inputs["context"]["stadium"], "잠실야구장")
        # 과거 checkpoint 는 thread 삭제 전까지 남는다 (seed 1 + 수정 pending 1 + completed 1)
        self.assertEqual(len(ChatThread(self.session.id).history()), 3)

    def test_put_invalid_context_rejected_without_truncating(self):
        """PUT context 검증은 view 에서 message_update() 호출 전에 일어나야 한다.
        검증 실패(400) 시 스냅샷이 전혀 바뀌지 않아야 한다 (검증이 절단보다 먼저)."""
        target = seed(self.session, ("이전 질문", "이전 답변"), ("수정 대상 질문", "수정 전 답변"))[2]
        before = snapshot(self.session)

        fake = FakeChain(chunks=("안",))
        with mock_patch("llm.service.chat.get_chain", return_value=fake):
            response = self.client_a.put(
                f"/api/v2/chat/sessions/{self.session.id}/messages/",
                {"content": "수정된 질문", "message_id": target.id,
                 "context": {"intent": "not-a-real-intent"}},
                format="json", HTTP_ACCEPT="text/event-stream",
            )

        self.assertEqual(response.status_code, 400)
        self.assertIsNone(fake.received_inputs)  # 체인까지 안 갔다
        self.assertEqual(snapshot(self.session), before)
        self.assertEqual(len(ChatThread(self.session.id).history()), 1)


class MessageIdValidationTest(CheckpointTestCase):
    """PUT/DELETE 의 message_id 가 malformed/missing/타입이 안 맞으면 서비스 호출(수정/삭제) 전에 400."""

    def setUp(self):
        self.client_a = APIClient()
        self.client_a.cookies["guest_id"] = "eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee"
        self.session = ChatSession.objects.create(guest="eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee")
        self.target = seed(self.session, ("수정 대상", "답변"))[0]
        self.before = snapshot(self.session)

    def assertUntouched(self):
        self.assertEqual(snapshot(self.session), self.before)

    def test_put_missing_message_id_rejected_without_deleting(self):
        fake = FakeChain(chunks=("답",))
        with mock_patch("llm.service.chat.get_chain", return_value=fake):
            response = self.client_a.put(
                f"/api/v2/chat/sessions/{self.session.id}/messages/",
                {"content": "수정된 질문"},
                format="json", HTTP_ACCEPT="text/event-stream",
            )
        self.assertEqual(response.status_code, 400)
        self.assertIsNone(fake.received_inputs)
        self.assertUntouched()

    def test_put_malformed_message_id_rejected_without_deleting(self):
        fake = FakeChain(chunks=("답",))
        with mock_patch("llm.service.chat.get_chain", return_value=fake):
            response = self.client_a.put(
                f"/api/v2/chat/sessions/{self.session.id}/messages/",
                {"content": "수정된 질문", "message_id": "not-a-valid-id"},
                format="json", HTTP_ACCEPT="text/event-stream",
            )
        self.assertEqual(response.status_code, 400)
        self.assertIsNone(fake.received_inputs)
        self.assertUntouched()

    def test_delete_missing_message_id_rejected_without_deleting(self):
        response = self.client_a.delete(
            f"/api/v2/chat/sessions/{self.session.id}/messages/",
            {}, format="json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertUntouched()

    def test_delete_malformed_message_id_rejected_without_deleting(self):
        response = self.client_a.delete(
            f"/api/v2/chat/sessions/{self.session.id}/messages/",
            {"message_id": "not-a-valid-id"}, format="json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertUntouched()

    def test_delete_valid_message_id_deletes(self):
        response = self.client_a.delete(
            f"/api/v2/chat/sessions/{self.session.id}/messages/",
            {"message_id": self.target.id}, format="json",
        )
        self.assertEqual(response.status_code, 204)
        self.assertEqual(snapshot(self.session), ([], {}))
        self.assertEqual(len(ChatThread(self.session.id).history()), 2)  # RemoveMessage 로 지웠고 thread 는 남는다


class UrlVersionForwardingTest(CheckpointTestCase):
    """api/v1/chat/, api/v2/chat/ 가 URL 의 version 을 그대로 서비스까지 넘기는지 HTTP 경로로 확인.
    llm/urls.py 안의 re_path(r"^(?P<version>v1|v2)/chat/", ...) 하나가 v1/v2 공통 회귀 테스트
    대상이다 (config/urls.py 는 plain path("api/", include("llm.urls")) 만 한다)."""

    def setUp(self):
        self.client_a = APIClient()
        self.client_a.cookies["guest_id"] = "ffffffff-ffff-ffff-ffff-ffffffffffff"
        self.session = ChatSession.objects.create(guest="ffffffff-ffff-ffff-ffff-ffffffffffff")

    def test_v1_url_forwards_v1_to_chain_selection(self):
        fake = FakeChain(chunks=("안",))
        with mock_patch("llm.service.chat.get_chain", return_value=fake) as get_chain_mock:
            response = self.client_a.post(
                f"/api/v1/chat/sessions/{self.session.id}/messages/",
                {"content": "안녕"}, format="json", HTTP_ACCEPT="text/event-stream",
            )
            self.assertEqual(response.status_code, 200)
            b"".join(response.streaming_content)
        get_chain_mock.assert_called_once_with("v1")

    def test_v2_url_forwards_v2_to_chain_selection(self):
        fake = FakeChain(chunks=("안",))
        with mock_patch("llm.service.chat.get_chain", return_value=fake) as get_chain_mock:
            response = self.client_a.post(
                f"/api/v2/chat/sessions/{self.session.id}/messages/",
                {"content": "안녕"}, format="json", HTTP_ACCEPT="text/event-stream",
            )
            self.assertEqual(response.status_code, 200)
            b"".join(response.streaming_content)
        get_chain_mock.assert_called_once_with("v2")

    def test_url_version_overrides_env_default(self):
        """env 가 v2 여도 /api/v1/chat/ 로 오면 v1 체인이 선택된다 (URL 이 우선)."""
        fake = FakeChain(chunks=("안",))
        with mock_patch.dict(os.environ, {"LLM_CHAIN_VERSION": "v2"}):
            with mock_patch("llm.service.chat.get_chain", return_value=fake) as get_chain_mock:
                response = self.client_a.post(
                    f"/api/v1/chat/sessions/{self.session.id}/messages/",
                    {"content": "안녕"}, format="json", HTTP_ACCEPT="text/event-stream",
                )
                self.assertEqual(response.status_code, 200)
                b"".join(response.streaming_content)
        get_chain_mock.assert_called_once_with("v1")

    def test_unsupported_version_in_url_rejected_without_inserting_message(self):
        """api/v3/chat/... 는 llm/urls.py 안의 re_path 정규식(v1|v2)이 애초에 라우팅하지
        않는다 (Resolver404) -- 요청이 view/message insert 까지 도달하지 않는다."""
        from django.urls import Resolver404, resolve

        with self.assertRaises(Resolver404):
            resolve(f"/api/v3/chat/sessions/{self.session.id}/messages/")
        self.assertEqual(snapshot(self.session), ([], {}))

    def test_direct_caller_unsupported_version_rejected_without_silent_fallback(self):
        """URL 정규식 밖에서 호출되는 경로(관리 커맨드 등)를 위한 방어: get_chain()/resolve_version()
        은 v1/v2 이외 값을 v2 로 조용히 폴백하지 않고 ValueError 로 막는다."""
        with self.assertRaises(ValueError):
            chat_service.resolve_version("v3")
        with self.assertRaises(ValueError):
            chat_service.get_chain("v3")

    def test_put_propagates_version_and_invalid_version_makes_no_destructive_changes(self):
        """PUT 도 URL version 을 체인 선택까지 전달한다. 잘못된 버전 URL 은 라우팅조차 안 된다."""
        target = seed(self.session, ("수정 대상", "답변"))[0]
        fake = FakeChain(chunks=("안",))
        with mock_patch("llm.service.chat.get_chain", return_value=fake) as get_chain_mock:
            response = self.client_a.put(
                f"/api/v1/chat/sessions/{self.session.id}/messages/",
                {"content": "수정된 질문", "message_id": target.id},
                format="json", HTTP_ACCEPT="text/event-stream",
            )
            self.assertEqual(response.status_code, 200)
            b"".join(response.streaming_content)
        get_chain_mock.assert_called_once_with("v1")
        self.assertEqual([i["content"] for i in history(self.session)], ["수정된 질문", "안"])

        # api/v3/chat/... 같은 잘못된 버전 PUT 은 URLconf 단계에서 이미 막힌다 (Resolver404).
        from django.urls import Resolver404, resolve

        other_session = ChatSession.objects.create(guest="ffffffff-ffff-ffff-ffff-fffffffffffe")
        seed(other_session, ("지워지면 안 되는 메시지", "답변"))
        with self.assertRaises(Resolver404):
            resolve(f"/api/v3/chat/sessions/{other_session.id}/messages/")
        self.assertEqual(len(history(other_session)), 2)


class PausingChain:
    """delta 한 개를 낸 뒤 멈춰서, 그 사이 동시 요청을 흉내낼 수 있게 하는 가짜 체인."""

    def __init__(self, chunks=("안", "녕")):
        self.chunks = chunks
        self.received_inputs = None

    def stream(self, inputs):
        self.received_inputs = inputs
        yield from self.chunks


class StreamConcurrencyTest(CheckpointTestCase):
    """같은 대화 동시 요청은 직렬화하지 않는다(ChatThread 의 ponytail). 스트림 종료/실패/취소 경로만 확인한다."""

    def setUp(self):
        self.client_a = APIClient()
        self.client_a.cookies["guest_id"] = "99999999-9999-9999-9999-999999999999"
        self.session = ChatSession.objects.create(guest="99999999-9999-9999-9999-999999999999")
        self.url = f"/api/v2/chat/sessions/{self.session.id}/messages/"

    def test_other_session_is_not_blocked(self):
        other = ChatSession.objects.create(guest="99999999-9999-9999-9999-999999999999")
        with mock_patch("llm.service.chat.get_chain", return_value=PausingChain()):
            events = chat_service.send_message(self.session, "질문")
            next(events)
            frames = list(chat_service.send_message(other, "다른 방 질문"))
            list(events)
        self.assertEqual(frames[-1][0], "done")

    def test_failure_mid_stream_marks_failed(self):
        class PausingFailingChain:
            def stream(self, inputs):
                yield "안"
                raise RuntimeError("provider exploded mid-stream")

        with mock_patch("llm.service.chat.get_chain", return_value=PausingFailingChain()):
            frames = list(chat_service.send_message(self.session, "질문"))
        self.assertEqual([e for e, _ in frames], ["delta", "error"])
        self.assertNotIn("provider exploded", json.dumps(frames, ensure_ascii=False))
        messages, turns = snapshot(self.session)
        self.assertEqual([m.content for m in messages], ["질문"])  # 부분 답변 저장 안 함
        self.assertEqual(turns[messages[0].id]["status"], "failed")


class ChainInitFailureTest(CheckpointTestCase):
    """defect #3 회귀: get_chain() 초기화 실패가 try 밖에서 튀어오르면 질문 턴이 pending 에 갇히고
    SSE 응답이 500 traceback 으로 끝난다. 다른 provider 실패와 똑같이 failed + error 여야 한다."""

    def setUp(self):
        self.session = ChatSession.objects.create(guest="88888888-8888-8888-8888-888888888888")

    def test_chain_initialization_error_marks_user_message_failed_and_emits_error(self):
        with mock_patch("llm.service.chat.get_chain", side_effect=RuntimeError("chain import 실패")):
            frames = list(chat_service.send_message(self.session, "질문"))

        self.assertEqual(frames, [("error", {"detail": GENERIC_ERROR_MESSAGE})])
        messages, turns = snapshot(self.session)
        self.assertEqual(turns[messages[0].id], {"status": "failed", "answer_id": None})
        self.assertEqual([i["status"] for i in history(self.session)], ["failed"])

    def test_chain_initialization_error_over_http_returns_valid_sse_not_500(self):
        """HTTP 경로: Accept: text/event-stream 요청이 초기화 실패에도 500 traceback 이
        아니라 유효한 SSE error 프레임(JSON body)으로 끝나야 한다."""
        client = APIClient()
        client.cookies["guest_id"] = "77777777-7777-7777-7777-777777777777"
        session = ChatSession.objects.create(guest="77777777-7777-7777-7777-777777777777")

        with mock_patch("llm.service.chat.get_chain", side_effect=RuntimeError("chain import 실패")):
            response = client.post(
                f"/api/v2/chat/sessions/{session.id}/messages/",
                {"content": "질문"}, format="json", HTTP_ACCEPT="text/event-stream",
            )
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response["Content-Type"], "text/event-stream")
            body = b"".join(response.streaming_content).decode("utf-8")

        frames = _read_sse_body(body)
        self.assertEqual([event for event, _ in frames], ["error"])
        self.assertEqual(frames[0][1], {"detail": GENERIC_ERROR_MESSAGE})


class MessageDeleteNotFoundTest(CheckpointTestCase):
    """존재하지 않는/다른 세션의/user 가 아닌 message_id 는 404 여야 한다 (500 아님)."""

    def setUp(self):
        self.client_a = APIClient()
        self.client_a.cookies["guest_id"] = "66666666-6666-6666-6666-666666666666"
        self.session = ChatSession.objects.create(guest="66666666-6666-6666-6666-666666666666")
        self.url = f"/api/v2/chat/sessions/{self.session.id}/messages/"

    def test_delete_nonexistent_message_id_returns_404_not_500(self):
        response = self.client_a.delete(self.url, {"message_id": str(uuid.uuid4())}, format="json")
        self.assertEqual(response.status_code, 404)

    def test_put_nonexistent_message_id_returns_404_not_500(self):
        seed(self.session, ("질문", "답변"))
        before = snapshot(self.session)
        fake = FakeChain(chunks=("안",))
        with mock_patch("llm.service.chat.get_chain", return_value=fake):
            response = self.client_a.put(
                self.url, {"content": "질문", "message_id": str(uuid.uuid4())},
                format="json", HTTP_ACCEPT="text/event-stream",
            )
        self.assertEqual(response.status_code, 404)
        self.assertIsNone(fake.received_inputs)
        self.assertEqual(snapshot(self.session), before)

    def test_delete_foreign_session_message_id_returns_404_not_500(self):
        """message_id 가 실존하지만 다른 세션 소유이면 404 (본인 세션 것처럼 지울 수 없다)."""
        other_session = ChatSession.objects.create(guest="55555555-5555-5555-5555-555555555555")
        foreign = seed(other_session, ("다른 세션 메시지", "답변"))[0]
        response = self.client_a.delete(self.url, {"message_id": foreign.id}, format="json")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(len(history(other_session)), 2)

    def test_delete_assistant_role_message_id_returns_404_not_500(self):
        """message_id 가 실존해도 assistant 답변이면(사용자 메시지가 아니면) 404."""
        answer = seed(self.session, ("질문", "AI 답변"))[1]
        response = self.client_a.delete(self.url, {"message_id": answer.id}, format="json")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(len(history(self.session)), 2)
