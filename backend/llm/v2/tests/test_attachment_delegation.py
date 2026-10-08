"""Mandatory URL delegation through the real ToolNode and public stream."""
import json
import os
import uuid
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from unittest.mock import patch

from django.test import TransactionTestCase
from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage, ToolMessage
from langchain_core.outputs import ChatGenerationChunk

from llm.models import ChatAttachment, ChatSession
from llm.serializer.message import project_history
from llm.service import attachments, chat_v2
from llm.service.chat_runs import Stopped
from llm.v2.agent import browser_research, chain
from llm.v2.tests.test_chain import ScriptedModel, call, fake_tools
from llm.v2.tests.test_web_specialist import observed


class DelayedStreamingModel(ScriptedModel):
    release: object
    completed: object

    def _stream(self, messages, stop=None, run_manager=None, **kwargs):
        response = self._generate(messages, stop=stop, run_manager=run_manager, **kwargs).generations[0].message
        yield ChatGenerationChunk(message=AIMessageChunk(response.text))
        if response.text == "SUPPORTED_FINDINGS":
            if not self.release.wait(10):
                raise AssertionError("specialist provider not released")
            self.completed.set()


class AttachmentDelegationTests(TransactionTestCase):
    def setUp(self):
        self.session = ChatSession.objects.create(guest=uuid.uuid4())
        self.rows = [ChatAttachment.objects.create(session=self.session, kind="url", name=f"page {i}",
                     source_url=f"https://example.com/page{i}") for i in range(3)]

    def input(self, rows=None):
        return {"messages": [HumanMessage("PRIVATE_QUESTION 잠실 휴게시간?", id="q", additional_kwargs={
            "attachment_ids": [str(r.id) for r in (self.rows if rows is None else rows)]})],
            "attachment_session_id": str(self.session.id)}

    def body(self, url):
        return observed("BEGIN " + "baseball " * 600 + " MIDDLE 14:00 ~ 15:00 " + "seats " * 600 + " END",
                        requested_url=url, final_url=url, source_url=url)

    def test_real_delegation_single_model_before_main_and_duplicate_blocked(self):
        for enabled in ("false", "true"):
            calls = []
            model = ScriptedModel(script=[AIMessage("SUPPORTED_FINDINGS 14:00 ~ 15:00"),
                call("ask_web_research", {"task": "forged extra search"}, "forged"), AIMessage("MAIN_FINAL")], calls=calls)
            with patch.dict(os.environ, {"WEB_RESEARCH_ENABLED": enabled}), patch(
                    "llm.v2.agent.web_sub_agent.research_model", return_value=model), patch(
                    "llm.v2.agent.web_sub_agent.direct_tools", return_value=[]), patch(
                    "llm.v2.middleware.jev_guidelines.classify", return_value={"allowed": True, "capabilities": []}) as classify, patch.object(
                    browser_research, "web_body", side_effect=self.body) as reader:
                out = chain.build_graph(model, fake_tools([])).invoke(self.input())
            classify.assert_called_once()
            self.assertEqual(reader.call_count, 3 if enabled == "false" else 0)
            self.assertEqual(calls[0]["tools"], ())
            self.assertEqual(len(calls), 3)
            self.assertIn("PRIVATE_QUESTION", calls[0]["messages"][1].text)
            for r in self.rows:
                self.assertIn(r.source_url, calls[0]["messages"][1].text)
            self.assertNotIn("ask_web_research", calls[1]["tools"])
            current = next(m for m in calls[1]["messages"] if m.id == "q")
            self.assertIn("BEGIN", current.text)
            self.assertIn("MIDDLE 14:00 ~ 15:00", current.text)
            self.assertIn(" END", current.text)
            results = [m for m in out["messages"] if isinstance(m, ToolMessage)]
            self.assertEqual(results[0].content, "SUPPORTED_FINDINGS 14:00 ~ 15:00")
            self.assertIsInstance(results[0].artifact, list)
            self.assertEqual(results[1].status, "error")
            self.assertEqual(out["messages"][-1].text, "MAIN_FINAL")
            public_call = next(m for m in out["messages"] if isinstance(m, AIMessage) and m.tool_calls).tool_calls[0]
            self.assertEqual(public_call["args"], {"task": "첨부 페이지 내용 조사", "summary": "첨부 페이지 내용 조사"})

    def test_citations_current_multiple_and_cached_followup_use_only_passed_originals(self):
        unrelated = ChatAttachment.objects.create(session=self.session, kind="url", name="unrelated",
                                                  source_url="https://example.com/unrelated", extracted_text="unrelated")
        calls = []
        model = ScriptedModel(script=[AIMessage("findings"), AIMessage("MAIN"), AIMessage("FOLLOWUP")], calls=calls)
        with patch.dict(os.environ, {"WEB_RESEARCH_ENABLED": "false"}), patch(
                "llm.v2.middleware.jev_guidelines.classify", return_value={"allowed": True, "capabilities": []}), patch.object(
                browser_research, "web_body", side_effect=self.body) as reader, patch(
                "llm.v2.agent.chain.get_graph", return_value=chain.build_graph(model, fake_tools([]))):
            first = {"answer": "", "messages": []}
            first_frames = list(chat_v2._frames(self.input(), first))
            second = {"answer": "", "messages": []}
            second_frames = list(chat_v2._frames({**self.input(), "messages": [*self.input()["messages"],
                AIMessage(first["answer"]), HumanMessage("followup", id="next")]}, second))
        self.assertEqual(reader.call_count, 3)
        self.assertEqual(len(calls), 3)  # No specialist rerun for historical-only followup.
        for run, frames in ((first, first_frames), (second, second_frames)):
            self.assertEqual("".join(d["text"] for k, d in frames if k == "delta" and not d.get("parent_id")), run["answer"])
            for row in self.rows:
                self.assertEqual(run["answer"].count(f"]({row.source_url})"), 1)
            self.assertNotIn(unrelated.source_url, run["answer"])

    def test_citations_exclude_failed_partial_and_generated_sources(self):
        def source(row, question=""):
            if row == self.rows[0]:
                return attachments.URLObservedBody("original", {"status": "ok"})
            if row == self.rows[1]:
                return attachments.URLObservedBody("partial", {"status": "partial"})
            return attachments.URLPageAnalysis("generated")
        with patch.dict(os.environ, {"WEB_RESEARCH_ENABLED": "false"}), patch(
                "llm.v2.middleware.jev_guidelines.classify", return_value={"allowed": True, "capabilities": []}), patch.object(
                attachments, "source_text", side_effect=source), patch("llm.v2.agent.chain.get_graph",
                return_value=chain.build_graph(ScriptedModel(script=[AIMessage("findings"), AIMessage("MAIN")], calls=[]), fake_tools([]))):
            run = {"answer": "", "messages": []}
            list(chat_v2._frames(self.input(), run))
        self.assertIn(f"]({self.rows[0].source_url})", run["answer"])
        for row in self.rows[1:]:
            self.assertNotIn(row.source_url, run["answer"])

    def test_recoverable_model_failure_empty_and_stop(self):
        def failed(messages):
            raise RuntimeError("private failure detail")
        def stopped(messages):
            raise Stopped()
        for response in (failed, AIMessage(""), stopped):
            calls = []
            with patch("llm.v2.middleware.jev_guidelines.classify", return_value={"allowed": True, "capabilities": []}), patch.object(
                    browser_research, "web_body", side_effect=self.body), patch.dict(os.environ, {"WEB_RESEARCH_ENABLED": "false"}):
                graph = chain.build_graph(ScriptedModel(script=[response, AIMessage("MAIN_FALLBACK")], calls=calls), fake_tools([]))
                if response is stopped:
                    with self.assertRaises(Stopped):
                        graph.invoke(self.input(self.rows[:1]))
                    self.assertEqual(len(calls), 1)
                    continue
                if response is failed:
                    # ScriptedModel keeps a callable on its script; make it fail only once.
                    def once(messages):
                        graph_model.script.pop(0)
                        raise RuntimeError("private failure detail")
                    graph_model = ScriptedModel(script=[once, AIMessage("MAIN_FALLBACK")], calls=calls)
                    graph = chain.build_graph(graph_model, fake_tools([]))
                out = graph.invoke(self.input(self.rows[:1]))
            result = next(m for m in out["messages"] if isinstance(m, ToolMessage))
            self.assertEqual(result.status, "error")
            self.assertNotIn("private failure detail", result.text)
            self.assertEqual(out["messages"][-1].text, "MAIN_FALLBACK")
            self.assertIn("MIDDLE 14:00 ~ 15:00", next(m for m in calls[-1]["messages"] if m.id == "q").text)

    def test_scope_ownership_and_limit_before_analysis(self):
        with patch.dict(os.environ, {"WEB_RESEARCH_ENABLED": "false"}), patch.object(browser_research, "web_body") as reader:
            for allowed, session in ((False, str(self.session.id)), (True, str(uuid.uuid4()))):
                calls = []
                with patch("llm.v2.middleware.jev_guidelines.classify", return_value={"allowed": allowed, "capabilities": []}):
                    data = {**self.input(), "attachment_session_id": session}
                    graph = chain.build_graph(ScriptedModel(script=[], calls=calls), fake_tools([]))
                    if allowed:
                        with self.assertRaises(ValueError):
                            graph.invoke(data)
                    else:
                        graph.invoke(data)
                self.assertEqual(calls, [])
            reader.assert_not_called()
        from llm.v2.middleware.attachment_context import DirectContextLimit
        with patch.dict(os.environ, {"WEB_RESEARCH_ENABLED": "false"}), patch(
                "llm.v2.middleware.jev_guidelines.classify", return_value={"allowed": True, "capabilities": []}), patch.object(
                browser_research, "web_body", return_value=observed("word " * 21000, "partial", requested_url=self.rows[0].source_url,
                final_url=self.rows[0].source_url, source_url=self.rows[0].source_url)):
            calls = []
            with self.assertRaises(DirectContextLimit):
                chain.build_graph(ScriptedModel(script=[], calls=calls), fake_tools([])).invoke(self.input(self.rows[:1]))
            self.assertEqual(calls, [])
        self.rows[0].refresh_from_db()
        self.assertEqual(self.rows[0].extracted_text, "")

    def test_historical_only_and_new_turn_can_run_again(self):
        calls = []
        model = ScriptedModel(script=[AIMessage("findings"), AIMessage("main"), AIMessage("followup"),
                                     AIMessage("new findings"), AIMessage("new main")], calls=calls)
        with patch.dict(os.environ, {"WEB_RESEARCH_ENABLED": "false"}), patch(
                "llm.v2.middleware.jev_guidelines.classify", return_value={"allowed": True, "capabilities": []}), patch.object(
                browser_research, "web_body", side_effect=self.body) as reader:
            graph = chain.build_graph(model, fake_tools([]))
            first = graph.invoke(self.input(self.rows[:1]))
            second = graph.invoke({**self.input(self.rows[:1]), "messages": [*first["messages"], HumanMessage("followup", id="next")]})
            self.assertFalse(second["attachment_web_done"])
            graph.invoke({**self.input(self.rows[:1]), "messages": [*second["messages"], HumanMessage("reattach", id="new",
                         additional_kwargs={"attachment_ids": [str(self.rows[0].id)]})]})
            reader.assert_called_once()
        self.assertEqual(len(calls), 5)

    def test_source_status_failure_frames_and_reader_cancellation(self):
        for status in ("busy", "timeout", "partial", "cancelled"):
            row = self.rows[0]
            calls = []
            model = ScriptedModel(script=[AIMessage("analysis of missing evidence"), AIMessage("MAIN_FALLBACK")], calls=calls)
            with patch.dict(os.environ, {"WEB_RESEARCH_ENABLED": "false"}), patch(
                    "llm.v2.middleware.jev_guidelines.classify", return_value={"allowed": True, "capabilities": []}), patch.object(
                    browser_research, "web_body", return_value={"status": status}), patch(
                    "llm.v2.agent.chain.get_graph", return_value=chain.build_graph(model, fake_tools([]))):
                run = {"answer": "", "messages": []}
                if status == "cancelled":
                    with self.assertRaises(Stopped):
                        list(chat_v2._frames(self.input([row]), run))
                    self.assertEqual(calls, [])
                else:
                    frames = list(chat_v2._frames(self.input([row]), run))
                    self.assertEqual([data["status"] for kind, data in frames if kind == "tool"], ["running", "failed"])
                    self.assertIn(f'"status": "{status}"', next(m for m in calls[-1]["messages"] if m.id == "q").text)
                    self.assertEqual(run["answer"], "MAIN_FALLBACK")
            row.refresh_from_db()
            self.assertEqual(row.extracted_text, "")

    def test_no_attachment_and_text_only_do_not_delegate(self):
        row = ChatAttachment.objects.create(session=self.session, kind="text", name="notes")
        for selected in ([], [row]):
            calls = []
            with patch.dict(os.environ, {"WEB_RESEARCH_ENABLED": "false"}), patch(
                    "llm.v2.middleware.jev_guidelines.classify", return_value={"allowed": True, "capabilities": []}), patch.object(
                    attachments, "source_text", return_value="plain text"), patch.object(browser_research, "web_body") as reader:
                out = chain.build_graph(ScriptedModel(script=[AIMessage("MAIN")], calls=calls), fake_tools([])).invoke(self.input(selected))
            self.assertEqual(len(calls), 1)
            self.assertFalse(any(isinstance(m, ToolMessage) for m in out["messages"]))
            reader.assert_not_called()

    def test_concurrent_requests_keep_originals_and_forced_ids_isolated(self):
        def execute(row):
            calls = []
            graph = chain.build_graph(ScriptedModel(script=[AIMessage("findings"), AIMessage("main")], calls=calls), fake_tools([]))
            out = graph.invoke(self.input([row]))
            submitted = next(m for m in calls[-1]["messages"] if m.id == "q").text
            return out["attachment_web_call_id"], submitted
        with patch.dict(os.environ, {"WEB_RESEARCH_ENABLED": "false"}), patch(
                "llm.v2.middleware.jev_guidelines.classify", return_value={"allowed": True, "capabilities": []}), patch.object(
                browser_research, "web_body", side_effect=self.body), ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(execute, self.rows[:2]))
        self.assertNotEqual(results[0][0], results[1][0])
        for index, (_, submitted) in enumerate(results):
            self.assertIn(self.rows[index].source_url, submitted)
            self.assertNotIn(self.rows[1 - index].source_url, submitted)

    def test_streaming_provider_delta_precedes_specialist_completion(self):
        self._assert_delayed_stream(responses=True)

    def test_base_chat_model_delta_precedes_specialist_completion(self):
        self._assert_delayed_stream(responses=False)

    def _assert_delayed_stream(self, responses):
        release, completed = Event(), Event()
        from types import SimpleNamespace
        from langchain_openai import ChatOpenAI
        from contextlib import contextmanager, nullcontext
        replies = iter(("SUPPORTED_FINDINGS", "MAIN_FINAL"))
        @contextmanager
        def provider_stream(*args, **kwargs):
            text = next(replies)
            def events():
                yield SimpleNamespace(type="response.output_text.delta", output_index=0, content_index=0, delta=text)
                if text == "SUPPORTED_FINDINGS":
                    if not release.wait(10):
                        raise AssertionError("specialist provider not released")
                    completed.set()
            yield events()
        model = ChatOpenAI(model="gpt-6-luna", api_key="offline-test-placeholder", use_responses_api=True,
                           max_tokens=4000, max_retries=0)
        provider = patch("openai.resources.responses.responses.Responses.create", side_effect=provider_stream)
        if responses:
            from llm.service import usage
            meter = usage.Meter()
            token = usage._meter.set(meter)
            self.addCleanup(usage._meter.reset, token)
        else:
            model = DelayedStreamingModel(script=[AIMessage("SUPPORTED_FINDINGS"), AIMessage("MAIN_FINAL")],
                                          calls=[], release=release, completed=completed)
            provider = nullcontext()
        run = {"answer": "", "messages": []}
        with provider, patch.dict(os.environ, {"WEB_RESEARCH_ENABLED": "false"}), patch(
                "llm.v2.middleware.jev_guidelines.classify", return_value={"allowed": True, "capabilities": []}), patch.object(
                browser_research, "web_body", side_effect=self.body), patch("llm.v2.agent.chain.get_graph",
                return_value=chain.build_graph(model, fake_tools([]))):
            frames = chat_v2._frames(self.input(self.rows[:1]), run)
            first = next(frames)
            with ThreadPoolExecutor(max_workers=1) as pool:
                pending = pool.submit(next, frames)
                try:
                    kind, delta = pending.result(timeout=5)
                    self.assertEqual(kind, "delta")
                    self.assertEqual(delta, {"text": "SUPPORTED_FINDINGS", "parent_id": first[1]["id"]})
                    self.assertFalse(completed.is_set())
                finally:
                    release.set()
                rest = list(frames)
        self.assertTrue(completed.is_set())
        self.assertEqual(run["answer"], "MAIN_FINAL\n\n출처: [page 0](https://example.com/page0)")
        self.assertEqual("".join(d["text"] for k, d in rest if k == "delta" and not d.get("parent_id")), run["answer"])
        result = next(m for m in run["messages"] if isinstance(m, ToolMessage))
        self.assertEqual(result.artifact[-1].text, "SUPPORTED_FINDINGS")
        public = project_history([self.input()["messages"][0], *run["messages"], AIMessage(run["answer"], id="answer")],
                                 {"q": {"status": "completed", "answer_id": "answer"}})
        self.assertIn("SUPPORTED_FINDINGS", json.dumps(public, ensure_ascii=False))

    def test_actual_frames_running_precedes_blocked_reader_and_history(self):
        entered, release = Event(), Event()
        def blocked(url):
            entered.set()
            if not release.wait(10):
                raise AssertionError("reader not released")
            return self.body(url)
        calls = []
        model = ScriptedModel(script=[AIMessage("SUPPORTED_FINDINGS"), AIMessage("MAIN_FINAL")], calls=calls)
        run = {"answer": "", "messages": []}
        with patch.dict(os.environ, {"WEB_RESEARCH_ENABLED": "false"}), patch(
                "llm.v2.middleware.jev_guidelines.classify", return_value={"allowed": True, "capabilities": []}), patch.object(
                browser_research, "web_body", side_effect=blocked), patch("llm.v2.agent.chain.get_graph",
                return_value=chain.build_graph(model, fake_tools([]))):
            frames = chat_v2._frames(self.input(self.rows[:1]), run)
            first = next(frames)
            self.assertEqual(first[0], "tool")
            self.assertEqual(first[1]["status"], "running")
            self.assertEqual(first[1]["kind"], "sub_agent")
            self.assertEqual(calls, [])
            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(list, frames)
                try:
                    self.assertTrue(entered.wait(5))
                    self.assertFalse(future.done())
                finally:
                    release.set()
                rest = future.result(timeout=20)
        specialist = [data for kind, data in rest if kind == "delta" and data.get("parent_id")]
        self.assertTrue(specialist)
        self.assertEqual(specialist[0]["parent_id"], first[1]["id"])
        self.assertEqual(run["answer"], "MAIN_FINAL\n\n출처: [page 0](https://example.com/page0)")
        self.assertEqual([data["status"] for kind, data in rest if kind == "tool"], ["completed"])
        answer = AIMessage(run["answer"], id="answer")
        public = project_history([self.input()["messages"][0], *run["messages"], answer],
                                 {"q": {"status": "completed", "answer_id": "answer"}})
        serialized = json.dumps(public, ensure_ascii=False)
        self.assertIn("SUPPORTED_FINDINGS", serialized)
        self.assertNotIn("BEGIN", serialized)
        self.assertNotIn("PRIVATE_QUESTION", json.dumps([first, *rest], ensure_ascii=False))
        self.assertEqual(public[-1]["content"], run["answer"])
