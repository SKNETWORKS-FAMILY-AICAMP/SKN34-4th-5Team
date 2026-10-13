"""chat_eval 일괄 평가 명령: 스트림 수집과 결정적 채점 (모델·네트워크 없음)."""
import io
import json
from unittest.mock import patch

from django.core.management import call_command
from django.test import SimpleTestCase
from langchain_core.messages import AIMessage, ToolMessage

from llm.management.commands import chat_eval


class FakeGraph:
    def __init__(self, answer, calls=(), sub_calls=()):
        self.answer, self.calls, self.sub_calls, self.inputs = answer, calls, sub_calls, []

    def stream(self, graph_input, **kwargs):
        self.inputs.append(graph_input)
        if self.calls:
            yield (), {"jev": {"decision": {"allowed": True, "capabilities": ["rules"]}}}
            yield (), {"model": {"messages": [AIMessage("", tool_calls=[{"name": n, "args": {"q": 1}, "id": n} for n in self.calls])]}}
            for n in self.sub_calls:
                yield ("tools:1",), {"model": {"messages": [AIMessage("", tool_calls=[{"name": n, "args": {}, "id": "s" + n}])]}}
            yield (), {"tools": {"messages": [ToolMessage("문서 내용", name=n, tool_call_id=n) for n in self.calls]}}
        yield (), {"model": {"messages": [AIMessage(self.answer)]}}


class ChatEvalTest(SimpleTestCase):
    def run_case(self, case, graph):
        turn, _ = chat_eval.run_turn(graph, [], case["turns"][-1])
        return turn, chat_eval.grade(case, turn)

    def test_collects_main_and_sub_agent_calls_and_grades_must(self):
        turn, fails = self.run_case({"turns": ["피치클락?"], "tools_any": ["search_kbo_documents"], "must": [["23초"], ["18초"]]},
                                    FakeGraph("주자 없을 때 18초, 있을 때 23초입니다.", ["search_kbo_documents"], ["get_games"]))
        self.assertEqual(fails, [])
        self.assertEqual([(c["name"], c["depth"]) for c in turn["tool_calls"]], [("search_kbo_documents", 0), ("get_games", 1)])
        self.assertEqual(turn["tool_results"][0]["content"], "문서 내용")
        self.assertEqual(turn["decision"]["capabilities"], ["rules"])
        _, fails = self.run_case({"turns": ["q"], "must": [["23초"]], "tools_any": ["get_weather"]}, FakeGraph("18초", ["search_kbo_documents"]))
        self.assertEqual(fails, ["필수 누락: 23초", "필요 도구 미호출: get_weather"])

    def test_clarify_refuse_url_and_video_rules(self):
        _, fails = self.run_case({"turns": ["q"], "expect": "clarify"}, FakeGraph("어느 구장인가요?", ["get_stadium"]))
        self.assertEqual(fails, ["되묻기 전에 조회함: get_stadium"])
        _, fails = self.run_case({"turns": ["q"], "expect": "clarify"}, FakeGraph("어느 구장인가요?"))
        self.assertEqual(fails, [])
        _, fails = self.run_case({"turns": ["q"], "expect": "clarify"}, FakeGraph("어느 구장 주변인지 알려주면 같이 골라볼게요!"))
        self.assertEqual(fails, [])
        _, fails = self.run_case({"turns": ["q"], "expect": "clarify"}, FakeGraph("잠실 주변 맛집은 진김밥이에요."))
        self.assertEqual(fails, ["되묻는 질문 없음"])
        _, fails = self.run_case({"turns": ["q"], "expect": "refuse"}, FakeGraph("KBO 직관 관련 질문만 도와드릴 수 있어요. 도와드리기 어렵습니다."))
        self.assertEqual(fails, [])
        _, fails = self.run_case({"turns": ["q"], "no_url": True, "videos": "required"}, FakeGraph("https://youtube.com/watch?v=x"))
        self.assertEqual(fails, ["답변 본문에 URL", "영상 카드 없음"])
        _, fails = self.run_case({"turns": ["q"], "tools_all": [["ask_highlight"], ["search_kbo_documents"]]}, FakeGraph("a", ["ask_highlight"]))
        self.assertEqual(fails, ["필요 도구 미호출: search_kbo_documents"])

    def test_clarify_input_requests_and_declaratives(self):
        for answer in (
            "어떤 구장이든 주변에서 식사하면 됩니다.",
            "어느 구장이나 주변에 맛집이 있습니다.",
            "메뉴는 직원이 알려주면 주문하면 됩니다.",
            "어느 구장인지 확인했습니다. 잠실입니다.",
        ):
            with self.subTest(answer=answer):
                _, fails = self.run_case({"turns": ["q"], "expect": "clarify"}, FakeGraph(answer))
                self.assertEqual(fails, ["되묻는 질문 없음"])
        for answer, calls in (
            ("어느 구장 주변인지 알려주면 같이 골라볼게요!", ()),
            ("가실 구장을 알려주시면 주변 식당을 골라드릴게요.", ()),
            ("가실 구장을 알려주세요.", ()),
            ("원하는 메뉴를 말씀해 주세요.", ()),
            ("가실 곳은 잠실인가요?", ()),
            ("구장을 선택해 주세요.", ("present_planning_questions",)),
        ):
            with self.subTest(answer=answer):
                _, fails = self.run_case({"turns": ["q"], "expect": "clarify"}, FakeGraph(answer, calls))
                self.assertEqual(fails, [])
        _, fails = self.run_case({"turns": ["q"], "expect": "clarify"},
                                 FakeGraph("가실 구장을 알려주세요.", ["get_stadium"]))
        self.assertEqual(fails, ["되묻기 전에 조회함: get_stadium"])

    def test_command_runs_multi_turn_and_writes_new_file(self):
        graph = FakeGraph("코스입니다", ["ask_course"])
        lines = "\n".join(json.dumps(c, ensure_ascii=False) for c in [
            {"id": "a", "category": "루트", "turns": ["코스 짜줘", "카페만 바꿔줘"], "tools_any": ["ask_course"]},
            {"id": "b", "category": "규정", "turns": ["q"], "must": [["없는말"]]}])
        out = io.StringIO()
        with patch("llm.v2.agent.chain.get_graph", return_value=graph), patch.object(chat_eval, "OUT") as path, \
                patch("sys.stdin", io.StringIO(lines)):
            written = io.StringIO()
            path.__truediv__.return_value.open.return_value.__enter__.return_value = written
            call_command("chat_eval", questions="-", label="T", stdout=out)
        self.assertEqual(len(graph.inputs), 3)  # 2턴 + 1턴
        self.assertEqual([m.content for m in graph.inputs[1]["messages"]], ["코스 짜줘", "코스입니다", "카페만 바꿔줘"])
        self.assertIn("1/2 통과", out.getvalue())
        self.assertIn("NG  b", out.getvalue())
