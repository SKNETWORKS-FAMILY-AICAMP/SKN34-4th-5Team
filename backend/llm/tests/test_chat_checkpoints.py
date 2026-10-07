"""실제 PostgreSQL 에서 ChatThread(compiled StateGraph + PostgresSaver) 저장/삭제/수정을 검증한다."""

import uuid
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest.mock import patch
from urllib.parse import quote, urlencode, urlsplit

from django.db import OperationalError, connection, connections, transaction
from django.http import Http404
from django.test import TestCase, TransactionTestCase
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from llm.models import ChatSession
from llm.service.chat import ChatThread


def _turn(question, answer, *, call_id, status="success"):
    return [
        HumanMessage(question, id=f"h-{call_id}"),
        AIMessage("", id=f"t-{call_id}", tool_calls=[{"name": "search_players", "args": {"name": "김도영"}, "id": call_id}]),
        ToolMessage('{"secret_internal": "raw row"}', id=f"r-{call_id}", tool_call_id=call_id, name="search_players", status=status),
        AIMessage(answer, id=f"a-{call_id}"),
    ]


def _turns(messages):
    return {m.id: {"status": "completed", "answer_id": f"a-{m.id[2:]}"} for m in messages if isinstance(m, HumanMessage)}


class ChatThreadTest(TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        ChatThread.setup()

    def setUp(self):
        self.session = ChatSession.objects.create(guest=uuid.uuid4())
        self.thread = ChatThread(self.session.id)
        self.addCleanup(self.thread.delete)

    def test_empty_thread(self):
        self.assertEqual(self.thread.state(), ([], {}))
        self.assertEqual(self.thread.history(), [])

    def test_roundtrip_two_turns_with_tools_via_new_graph(self):
        first, second = _turn("q1", "a1", call_id="c1"), _turn("q2", "a2", call_id="c2")
        self.assertTrue(self.thread.update(first, _turns(first)))
        self.assertTrue(self.thread.update(second, _turns(second)))  # add_messages 로 덧붙는다

        messages, turns = ChatThread(self.session.id).state()  # 새 연결/그래프로 복원
        self.assertEqual([m.id for m in messages], [m.id for m in first + second])
        self.assertEqual(messages[1].tool_calls[0]["args"], {"name": "김도영"})
        self.assertEqual(messages[2].content, '{"secret_internal": "raw row"}')  # 원본 보존
        self.assertEqual(turns, _turns(first + second))
        latest = self.thread.history()[0]
        self.assertEqual(latest.config["configurable"]["checkpoint_ns"], "")  # 최상위 namespace 만 쓴다
        self.assertEqual(len(self.thread.history()), 2)

    def test_delete_from_removes_target_and_after_keeps_past_checkpoints(self):
        both = _turn("q1", "a1", call_id="c1") + _turn("q2", "a2", call_id="c2")
        self.thread.update(both, _turns(both))

        self.assertEqual(self.thread.delete_from("h-c2"), 4)
        self.assertEqual(self.thread.state(), (both[:4], _turns(both[:4])))
        past = self.thread.history()
        self.assertEqual(len(past), 2)
        self.assertEqual(len(past[1].values["messages"]), 8)
        with self.assertRaises(Http404):  # AI 메시지/없는 id
            self.thread.delete_from("a-c1")

    def test_edit_keeps_id_and_removes_later_messages(self):
        both = _turn("q1", "a1", call_id="c1") + _turn("q2", "a2", call_id="c2")
        self.thread.update(both, _turns(both))

        prefix, prefix_turns, human = self.thread.edit("h-c1", "수정")
        self.assertEqual((prefix, prefix_turns, human.id), ([], {}, "h-c1"))
        messages, turns = self.thread.state()
        self.assertEqual([(m.id, m.content) for m in messages], [("h-c1", "수정")])
        self.assertEqual(turns, {"h-c1": {"status": "pending", "answer_id": None}})

    def test_delete_only_removes_that_thread_and_update_refuses_deleted_session(self):
        other_session = ChatSession.objects.create(guest=uuid.uuid4())
        other = ChatThread(other_session.id)
        self.addCleanup(other.delete)
        self.thread.update([HumanMessage("mine", id="h-mine")], {"h-mine": {"status": "pending", "answer_id": None}})
        other.update([HumanMessage("theirs", id="h-theirs")], {"h-theirs": {"status": "pending", "answer_id": None}})

        self.thread.delete()
        self.thread.delete()  # 재시도 안전
        self.assertEqual(self.thread.history(), [])
        self.assertEqual(other.state()[0][0].content, "theirs")

        with self.captureOnCommitCallbacks(execute=True):
            other_session.delete()  # post_delete outbox → commit 뒤 thread 도 지운다
        self.assertEqual(other.history(), [])
        self.assertFalse(other.update([HumanMessage("late", id="h-late")], {}))
        self.assertEqual(other.history(), [])  # 삭제된 세션에는 쓰지 않는다

    def test_concurrent_edits_from_same_base_first_wins_and_stale_writes_are_refused(self):
        """같은 base 를 읽은 요청들: 먼저 저장한 편집(A)만 revision 을 올린다. 진 편집(B)은 404, 편집 전에 읽은
        요청의 쓰기는 False 이고, 이긴 편집 쪽 이어 쓰기만 저장된다."""
        both = _turn("q1", "a1", call_id="c1") + _turn("q2", "a2", call_id="c2")
        self.thread.update(both, _turns(both))
        a, b, reader = (ChatThread(self.session.id) for _ in range(3))
        a.state(), b.state(), reader.state()
        a.edit("h-c1", "수정")
        with self.assertRaises(Http404):
            b.delete_from("h-c2")
        for status in ("completed", "cancelled"):
            self.assertFalse(reader.update([AIMessage("늦은 답", id=f"late-{status}")], {"h-c2": {"status": status, "answer_id": None}}))

        latest = ChatThread(self.session.id)
        self.assertEqual(latest.state(), ([HumanMessage("수정", id="h-c1")], {"h-c1": {"status": "pending", "answer_id": None}}))
        self.assertEqual(latest.revision, 1)
        self.assertIs(type(latest.state()[1]["h-c1"]["status"]), str)  # checkpoint 에 Enum 이 아닌 "pending"
        self.assertTrue(a.update([AIMessage("답", id="a-new")], {"h-c1": {"status": "completed", "answer_id": "a-new"}}))
        self.assertEqual([m.id for m in ChatThread(self.session.id).state()[0]], ["h-c1", "a-new"])


class ConcurrentChatThreadTest(TransactionTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        if connection.vendor != "postgresql":
            from unittest import SkipTest
            raise SkipTest("requires real PostgreSQL row locks and PostgresSaver")
        from llm.service.chat_thread import _conn_string

        # PostgresSaver opens its own connections, outside the ORM timeouts below.
        conn = urlsplit(_conn_string())
        conn = conn._replace(query=urlencode({"options": "-c statement_timeout=5000 -c lock_timeout=2000"}, quote_via=quote))
        saver_timeout = patch("llm.service.chat_thread._conn_string", return_value=conn.geturl())
        saver_timeout.start()
        cls.addClassCleanup(saver_timeout.stop)
        ChatThread.setup()

    def session(self):
        session = ChatSession.objects.create(guest=uuid.uuid4())
        self.addCleanup(ChatThread(session.id).delete)
        return session

    @staticmethod
    def connected(write):
        try:
            with connection.cursor() as cursor:
                cursor.execute("SET statement_timeout = '5s'")
                cursor.execute("SET lock_timeout = '2s'")
                cursor.execute("SELECT pg_backend_pid()")
                pid = cursor.fetchone()[0]
            return pid, write()
        finally:
            connections.close_all()

    def test_same_base_questions_and_forward_reverse_answers(self):
        from llm.serializer.message import wire_history

        for reverse in (False, True):
            with self.subTest(reverse=reverse):
                session = self.session()
                # A real checkpoint makes reverting to stale request anchors observable.
                ChatThread(session.id).update([], {})
                barrier = Barrier(2, timeout=5)

                class SameBaseThread(ChatThread):
                    def state(self):
                        result = super().state()
                        barrier.wait()  # both asks read the same checkpoint before either update
                        return result

                def ask(index):
                    thread = SameBaseThread(session.id)
                    _, _, human = thread.ask(f"q{index}")
                    return thread, human

                with ThreadPoolExecutor(max_workers=2) as pool:
                    futures = [pool.submit(self.connected, lambda i=i: ask(i)) for i in range(2)]
                    requests = [future.result(timeout=10) for future in futures]
                self.assertEqual(len({pid for pid, _ in requests}), 2)
                latest = ChatThread(session.id)
                questions, turns = latest.state()
                self.assertEqual({m.content for m in questions}, {"q0", "q1"})
                self.assertEqual(len(questions), 2)
                self.assertEqual({t["status"] for t in turns.values()}, {"pending"})
                question_wire = dict(latest.wire)
                self.assertEqual({entry["id"] for entry in question_wire.values()}, {1, 2})
                by_id = {human.id: thread for _, (thread, human) in requests}
                order = questions[::-1] if reverse else questions
                for human in order:
                    answer = AIMessage(f"a{human.content[1:]}", id=f"a-{human.id}")
                    thread = by_id[human.id]
                    with ThreadPoolExecutor(max_workers=1) as pool:
                        saved = pool.submit(self.connected, lambda: thread.update(
                            [answer], {human.id: {"status": "completed", "answer_id": answer.id}}))
                        self.assertTrue(saved.result(timeout=10)[1])
                latest = ChatThread(session.id)
                messages, turns = latest.state()
                self.assertEqual([m.content for m in messages],
                                 [text for q in questions for text in (q.content, f"a{q.content[1:]}")])
                self.assertEqual(turns, {q.id: {"status": "completed", "answer_id": f"a-{q.id}"}
                                         for q in questions})
                self.assertEqual({entry["id"] for entry in latest.wire.values()}, {1, 2, 3, 4})
                self.assertEqual([latest.wire[f"a-{q.id}"]["id"] for q in order], [3, 4])
                self.assertEqual({key: latest.wire[key] for key in question_wire}, question_wire)
                self.assertEqual([item["content"] for item in wire_history(messages, turns, latest.wire)],
                                 [m.content for m in messages])

    def test_row_fence_blocks_only_the_same_session(self):
        session, other = self.session(), self.session()
        with transaction.atomic():
            ChatSession.objects.select_for_update().get(pk=session.id)
            with ThreadPoolExecutor(max_workers=2) as pool:
                blocked = pool.submit(self.connected, lambda: ChatThread(session.id).ask("blocked"))
                independent = pool.submit(self.connected, lambda: ChatThread(other.id).ask("independent"))
                self.assertEqual(independent.result(timeout=5)[1][2].content, "independent")
                with self.assertRaises(OperationalError) as error:
                    blocked.result(timeout=5)
                self.assertEqual(error.exception.__cause__.sqlstate, "55P03")  # lock_not_available
            self.assertEqual(ChatThread(session.id).state(), ([], {}))
        ChatThread(session.id).ask("after unlock")
        self.assertEqual(ChatThread(session.id).state()[0][0].content, "after unlock")
