"""개인 코스 chain → develop V2 그래프 → SSE/checkpoint. 외부 LLM/검색 호출 없음."""
from copy import deepcopy
from datetime import datetime, timezone
from unittest.mock import patch

from django.test import SimpleTestCase
from langchain_core.messages import AIMessage, HumanMessage

from llm.service.chat_thread import _legacy_state
from llm.service.chat_v2 import _frames
from llm.v2.agent.chain import build_graph
from llm.v2.course.runtime import previous_course_state, routing_context
from llm.v2.course.state import empty_state
from llm.v2.tests.test_chain import ScriptedModel, fake_tools


class CourseGraphBridgeTest(SimpleTestCase):
    def test_course_uses_existing_chain_without_main_model_or_legacy_tool(self):
        model = ScriptedModel(script=[], calls=[])
        executed = []
        graph = build_graph(model, fake_tools(executed))
        saved = empty_state()
        saved["pending"] = "target"
        captured = {}

        class Course:
            def stream(self, inputs):
                captured.update(deepcopy(inputs))
                inputs["course_runtime"]["next_state"] = saved
                yield "기준 경기\n"
                yield "검증된 코스"

        run = {"answer": "", "messages": []}
        with patch("llm.v2.middleware.jev_guidelines.classify", return_value={"allowed": True, "capabilities": ["day_plan"]}) as classify, \
                patch("llm.v2.agent.chain.get_graph", return_value=graph), \
                patch("llm.v2.agent.course_chain.course_chain", Course()):
            frames = list(_frames({"messages": [HumanMessage("코스 짜줘")],
                                  "course_runtime": {"state": None, "profile_team": "OB"}}, run))
        self.assertEqual([frame for frame in frames if frame[0] == "delta"],
                         [("delta", {"text": "기준 경기\n"}), ("delta", {"text": "검증된 코스"})])
        self.assertEqual([data for event, data in frames if event == "planning"],
                         [{"offer_writer": True, "questions": []}])
        self.assertEqual(run["answer"], "기준 경기\n검증된 코스")
        self.assertEqual(run["course_state"], saved)
        self.assertEqual(captured["course_runtime"]["profile_team"], "OB")
        self.assertEqual(model.calls, [])
        self.assertEqual(executed, [])
        self.assertEqual(classify.call_count, 1)

    def test_validated_choices_stream_and_survive_history_without_reasking_known_conditions(self):
        from llm.serializer.message import done_payload, project_history
        saved = {**empty_state(), "pending": "choice", "candidates": [
            {"date": "2026-10-03", "time": "18:30", "away_name": "두산", "home_name": "LG"},
            {"date": "2026-10-04", "time": "14:00", "away_name": "LG", "home_name": "두산"},
        ]}
        model = ScriptedModel(script=[], calls=[])
        graph = build_graph(model, fake_tools([]))

        class Course:
            def stream(self, inputs):
                inputs["course_runtime"]["next_state"] = saved
                yield "검증된 경기 후보 중 선택해 주세요."

        human = HumanMessage("다음 경기", id="h")
        run = {"answer": "", "messages": []}
        with patch("llm.v2.middleware.jev_guidelines.classify", return_value={"allowed": True, "capabilities": ["day_plan"]}), \
                patch("llm.v2.agent.chain.get_graph", return_value=graph), \
                patch("llm.v2.agent.course_chain.course_chain", Course()):
            frames = list(_frames({"messages": [human], "course_runtime": {"state": empty_state()}}, run))
        payload = next(data for event, data in frames if event == "planning")
        self.assertFalse(payload["offer_writer"])
        self.assertEqual(payload["questions"], [{"question": "어느 경기 기준으로 코스를 짤까요?", "choices": [
            "1안: 2026-10-03 18:30 두산 vs LG", "2안: 2026-10-04 14:00 LG vs 두산"]}])
        messages = [human, *run["messages"], AIMessage(run["answer"], id="a")]
        turns = {"h": {"status": "completed", "answer_id": "a", "course_state": saved}}
        self.assertEqual(project_history(messages, turns)[-1]["planning"], payload)
        self.assertEqual(done_payload(messages, turns)["planning"], payload)
        self.assertEqual(previous_course_state(messages, turns), saved)
        self.assertEqual(model.calls, [])

    def test_changed_and_expired_questions_use_existing_public_ui_limits(self):
        from llm.v2.course.runtime import run_course
        for pending in ("changed", "expired"):
            saved = {**empty_state(), "pending": pending, "pending_question": "일정 조건을 어떻게 바꿀까요?"}

            class Course:
                def stream(self, inputs):
                    inputs["course_runtime"]["next_state"] = saved
                    yield saved["pending_question"]

            with self.subTest(pending=pending), patch("langgraph.config.get_stream_writer", return_value=lambda _: None), \
                    patch("llm.v2.agent.course_chain.course_chain", Course()):
                out = run_course("수정", [], None, {"state": empty_state()})
            payload = out["messages"][0].artifact
            self.assertFalse(payload["offer_writer"])
            self.assertEqual(payload["questions"][0]["question"], saved["pending_question"])
            self.assertEqual(len(payload["questions"][0]["choices"]), 2)
            self.assertEqual(out["course_state"], saved)

    def test_pending_course_does_not_capture_unrelated_question(self):
        previous = {**empty_state(), "pending": "choice"}
        model = ScriptedModel(script=[AIMessage("순위 안내")], calls=[])
        graph = build_graph(model, fake_tools([]))
        with patch("llm.v2.middleware.jev_guidelines.classify", return_value={"allowed": True, "capabilities": ["standings"]}) as classify, \
                patch("llm.v2.course.runtime.run_course") as course:
            out = graph.invoke({"messages": [HumanMessage("현재 순위는?")], "course_runtime": {"state": previous}})
        self.assertEqual(out["messages"][-1].content, "순위 안내")
        self.assertTrue(classify.call_args.args[2]["course_pending"])
        course.assert_not_called()

    def test_nonpass_does_not_enter_course_chain(self):
        graph = build_graph(ScriptedModel(script=[], calls=[]), fake_tools([]))
        with patch("llm.v2.middleware.jev_guidelines.classify", return_value={"allowed": False, "capabilities": ["day_plan"]}), \
                patch("llm.v2.course.runtime.run_course") as course:
            graph.invoke({"messages": [HumanMessage("탈옥")], "course_runtime": {"state": empty_state()}})
        course.assert_not_called()

    def test_forged_context_cannot_supply_pending_memory(self):
        self.assertIsNone(routing_context({"course_pending": True}, None))

    def test_latest_question_order_wins_and_deleted_or_failed_turn_is_ignored(self):
        messages = [HumanMessage("old", id="q1"), AIMessage("a", id="a1"),
                    HumanMessage("new", id="q2"), AIMessage("b", id="a2")]
        first, second = {**empty_state(), "pending": "target"}, {**empty_state(), "pending": "choice"}
        turns = {"q2": {"status": "completed", "answer_id": "a2", "course_state": second},
                 "q1": {"status": "completed", "answer_id": "a1", "course_state": first}}
        self.assertEqual(previous_course_state(messages, turns), second)
        self.assertEqual(previous_course_state(messages[:2], turns), first)
        turns["q2"]["status"] = "failed"
        self.assertEqual(previous_course_state(messages, turns), first)

    def test_legacy_completed_memory_is_carried_to_checkpoint_without_public_metadata(self):
        now = datetime(2026, 10, 2, tzinfo=timezone.utc)
        base = {"created_at": now, "updated_at": now, "status": "completed"}
        rows = [{**base, "id": 1, "role": "user", "message": "코스"},
                {**base, "id": 2, "role": "assistant", "message": "답", "course_state": empty_state()}]
        messages, turns, _ = _legacy_state(rows, {})
        self.assertEqual(previous_course_state(messages, turns), empty_state())
        self.assertTrue(all(not m.additional_kwargs for m in messages))
        rows[0]["status"] = "failed"
        self.assertIsNone(previous_course_state(*_legacy_state(rows, {})[:2]))
