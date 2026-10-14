"""Real compiled graph/model/Meter integration with synthetic SDK HTTP only."""
import tempfile
from types import SimpleNamespace
from typing import Annotated, TypedDict
from unittest.mock import Mock, patch

from django.test import SimpleTestCase, override_settings
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.graph import StateGraph, START, END
from langgraph.graph.message import add_messages

from llm import highlight_multimodal as mm
from llm.service import chat_runs
from llm.service.chat_v2 import _frames
from llm.service.usage import Meter
from llm.tests.test_highlight_langchain import SDKTransport, Trace, IMAGE, ROW, OUTPUT
from llm.tests.test_highlight_retry import SequentialTransport, call, verdict
from llm.v2.agent import highlight_sub_agent, sub_agents, chain


class State(TypedDict):
    messages: Annotated[list, add_messages]


class LangChainWebTests(SimpleTestCase):
    def test_actual_graph_delegate_meter_and_public_stream(self):
        google = mm.Google('synthetic', quota=Mock())
        meter, trace = Meter(), Trace()
        human = HumanMessage('find the play', id='current')
        restored = HumanMessage([{'type': 'text', 'text': human.text}, IMAGE], id=human.id)
        rows = [{**ROW, 'id': sid, 'video_id': f'SlvDBmr4Bd{i}', 'start': 0., 'end': 900.134}
                for i, sid in enumerate('abc')]
        def retrieve(query, candidate_limit, images):
            self.assertEqual((query, candidate_limit, images), (human.text, 3, [IMAGE]))
            return {'query': query, 'candidates': rows, 'retrieval_only': list('abc'),
                    'rankings': {'video': list('abc')}, 'retrieval_latency': 0}
        specialist = Mock(retrieve=retrieve)
        def generate_guard(*args):
            import fcntl
            specialist.close.assert_called_once()
            with open(root + '/web.lock', 'a') as lock:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        google.quota.acquire.side_effect = generate_guard
        with tempfile.TemporaryDirectory() as root, override_settings(HIGHLIGHT_MULTIMODAL_CORPUS=root), \
             patch('llm.highlight_multimodal.Google', return_value=google), \
             patch('llm.highlight_multimodal.Specialist', return_value=specialist), SequentialTransport([call(verdict('a', original_seconds=123.25, original_end_seconds=130.5, evidence=OUTPUT['evidence']))]) as transport:
            inner = highlight_sub_agent.build(None, {})
            def tools(state, config):
                runtime = SimpleNamespace(state={**state, 'attachment_messages': {human.id: restored}},
                    config=config, tool_call_id='outer')
                result = sub_agents._delegate('ask_highlight', 'highlight', inner).func('invented task', runtime)
                self.assertEqual(result.status, 'success', result.text)
                return {'messages': [result]}
            graph = StateGraph(State)
            graph.add_node('tools', tools)
            graph.add_node('model', lambda state: {'messages': [AIMessage('verified public answer')]})
            graph.add_edge(START, 'tools'); graph.add_edge('tools', 'model'); graph.add_edge('model', END)
            compiled = graph.compile().with_config(callbacks=[meter, trace])
            run = {'answer': '', 'messages': []}
            with patch.object(chain, 'get_graph', return_value=compiled):
                frames = list(_frames({'messages': [human]}, run))
        self.assertEqual(meter.totals(), (9, 4, 1, 0))
        self.assertEqual(len(trace.starts), 1)
        self.assertEqual(trace.starts[0]['metadata']['langgraph_node'], 'model')
        self.assertEqual(trace.starts[0]['metadata']['parent_id'], 'outer')
        self.assertEqual(trace.starts[0]['invocation_params']['_type'], 'chat-google-generative-ai')
        sources = [p for p in transport.requests[-1][1]['contents'][0]['parts'] if 'fileData' in p]
        self.assertEqual(len(sources), 1)
        self.assertEqual([op for op, _ in transport.requests], ['countTokens', 'generateContent'])
        self.assertEqual(run['answer'], 'verified public answer')
        public = str(frames)
        self.assertNotIn('candidate_id', public)
        self.assertNotIn('source_accessible', public)
        self.assertNotIn(IMAGE['image_url']['url'], public + str(run))
        self.assertNotIn(IMAGE['image_url']['url'].split(',')[1], public + str(run))
        outer = next(m for m in run['messages'] if isinstance(m, ToolMessage))
        self.assertEqual(outer.status, 'success')
        self.assertEqual(outer.artifact[-1].text, OUTPUT['evidence'])
        self.assertEqual(outer.artifact[-2].artifact['videos'][0]['start'], 123)
        specialist.close.assert_called_once()

    def test_actual_next_candidate_delegate_sdk_meter_and_block_status(self):
        for recover in (True, False):
            google = mm.Google('synthetic', quota=Mock())
            rows = [{**ROW, 'id': sid, 'video_id': f'SlvDBmr4Bd{i}', 'start': 0., 'end': 900.134}
                    for i, sid in enumerate('ab')]
            specialist = Mock()
            specialist.retrieve.return_value = {'query': 'original', 'candidates': rows,
                'retrieval_only': list('ab'), 'rankings': {'video': list('ab')}, 'retrieval_latency': 0}
            meter, trace = Meter(), Trace()
            human = HumanMessage('original', id='current')
            from llm.tests.test_highlight_images import IMAGE as original_image
            restored = HumanMessage([{'type': 'text', 'text': 'original'}, original_image], id=human.id)
            parts = [call(verdict('a', False, 'b', source_accessible=False)),
                     call(verdict('b', recover))]
            with self.subTest(recover=recover), tempfile.TemporaryDirectory() as root, \
                 override_settings(HIGHLIGHT_MULTIMODAL_CORPUS=root), \
                 patch('llm.highlight_multimodal.Google', return_value=google), \
                 patch('llm.highlight_multimodal.Specialist', return_value=specialist), \
                 SequentialTransport(parts) as transport:
                runtime = SimpleNamespace(state={'messages': [human], 'attachment_messages': {human.id: restored}},
                    config={'callbacks': [meter, trace]}, tool_call_id='outer')
                result = sub_agents._delegate('ask_highlight', 'highlight', highlight_sub_agent.build(None, {})).func('invented', runtime)
            specialist.retrieve.assert_called_once_with('original', candidate_limit=3, images=[original_image])
            self.assertEqual([op for op, _ in transport.requests], ['countTokens', 'generateContent'] * 2)
            self.assertEqual(meter.totals(), (18, 8, 2, 0))
            self.assertEqual(len(trace.starts), 2)
            self.assertTrue(all('nostream' in start['tags'] for start in trace.starts))
            self.assertNotIn(original_image['image_url']['url'].split(',')[1], str(result))
            for _, body in transport.requests:
                self.assertIn(original_image['image_url']['url'].split(',')[1], str(body))
            from llm.v2.progress import LABELS
            self.assertEqual(LABELS['review_highlight_candidate'], '하이라이트 영상 판정 중')
            self.assertEqual(LABELS['inspect_highlight_candidate'], '다음 하이라이트 후보 확인 중')
            self.assertEqual(result.additional_kwargs['highlight_status'], 'success' if recover else 'block')
            self.assertEqual(result.status, 'success' if recover else 'error')
            self.assertEqual(len(result.artifact[-2].artifact['videos']), 1 if recover else 0)
            self.assertEqual(result.artifact[-2].artifact['multimodal']['checked_candidates'], list('ab'))
            self.assertNotIn('source_accessible', result.text)

    def test_graph_verdict_boundaries_empty_and_no_unverified_cards(self):
        rows = [{**ROW, 'id': 'a', 'start': 0., 'end': 900.134}]
        fixtures = [(verdict(), False), (verdict(candidate_id='unknown'), True),
                    (verdict(original_end_seconds=901.), True), (verdict(source_accessible=False), True)]
        for output, failed in fixtures:
            google = mm.Google('synthetic', quota=Mock())
            specialist = Mock()
            specialist.retrieve.return_value = {'query': 'original', 'candidates': rows,
                'retrieval_only': ['a'], 'rankings': {'video': ['a']}, 'retrieval_latency': 0}
            with self.subTest(output=output), tempfile.TemporaryDirectory() as root, \
                 override_settings(HIGHLIGHT_MULTIMODAL_CORPUS=root), \
                 patch('llm.highlight_multimodal.Google', return_value=google), \
                 patch('llm.highlight_multimodal.Specialist', return_value=specialist), SequentialTransport([call(output)]) as transport:
                result = highlight_sub_agent.MultimodalAgent().invoke({'messages': [HumanMessage('invented')],
                    'highlight_original_text': 'original'})
            selection = result['messages'][-2]
            self.assertEqual(selection.status, 'error' if failed else 'success')
            self.assertEqual(len(selection.artifact.get('videos', [])), 0 if failed else 1)
            self.assertIs(selection.artifact['multimodal']['no_match'], None if failed else False)
            self.assertEqual([op for op, _ in transport.requests], ['countTokens', 'generateContent'])
            specialist.close.assert_called_once()
        google = mm.Google('synthetic', quota=Mock())
        specialist.retrieve.return_value['candidates'] = []
        with tempfile.TemporaryDirectory() as root, override_settings(HIGHLIGHT_MULTIMODAL_CORPUS=root), \
             patch('llm.highlight_multimodal.Google', return_value=google), \
             patch('llm.highlight_multimodal.Specialist', return_value=specialist), SDKTransport() as transport:
            result = highlight_sub_agent.MultimodalAgent().invoke({'messages': [HumanMessage('original')]})
        self.assertEqual(transport.requests, [])
        self.assertTrue(result['result']['no_match'])
        self.assertEqual(result['messages'][-2].artifact['videos'], [])

    def test_graph_model_cancellation_after_retrieval_and_quota(self):
        for stop_after_quota in (False, True):
            stopped = [False]
            rows = [{**ROW, 'id': sid, 'video_id': f'SlvDBmr4Bd{i}', 'start': 0., 'end': 900.134} for i, sid in enumerate('abc')]
            google = mm.Google('synthetic', quota=Mock(acquire=lambda *args: stopped.__setitem__(0, True)))
            def retrieve(*args, **kwargs):
                stopped[0] = not stop_after_quota
                return {'query': 'original', 'candidates': rows, 'retrieval_only': list('abc'),
                        'rankings': {'video': list('abc')}, 'retrieval_latency': 0}
            token = chat_runs._cancelled.set(lambda: stopped[0])
            try:
                with self.subTest(stop_after_quota=stop_after_quota), tempfile.TemporaryDirectory() as root, \
                     override_settings(HIGHLIGHT_MULTIMODAL_CORPUS=root), \
                     patch('llm.highlight_multimodal.Google', return_value=google), \
                     patch('llm.highlight_multimodal.Specialist', return_value=Mock(retrieve=retrieve)), \
                     SDKTransport() as transport, self.assertRaises(chat_runs.Stopped):
                    highlight_sub_agent.MultimodalAgent().invoke({'messages': [HumanMessage('original')]})
                self.assertEqual([op for op, _ in transport.requests], ['countTokens'] if stop_after_quota else [])
            finally:
                chat_runs._cancelled.reset(token)

    def test_real_model_meter_rejects_unsafe_override_before_generate(self):
        google = mm.Google('synthetic', quota=Mock())
        with override_settings(USAGE_MAX_CALL_OUTPUT_TOKENS=4000), SDKTransport() as transport:
            google.verify('find', ROW, None, None, config={'callbacks': [Meter()]})
        self.assertEqual(transport.requests[-1][1]['generationConfig']['maxOutputTokens'], 4000)
        with override_settings(USAGE_MAX_CALL_OUTPUT_TOKENS=0), SDKTransport() as transport:
            with self.assertRaises(RuntimeError):
                google.verify('find', ROW, None, None, config={'callbacks': [Meter()]})
        self.assertEqual([op for op, _ in transport.requests], ['countTokens'])

    def test_inherited_batch_cancellation_prevents_provider_calls(self):
        google = mm.Google('synthetic', quota=Mock())
        token = chat_runs._cancelled.set(lambda: True)
        try:
            with SDKTransport() as transport, self.assertRaises(chat_runs.Stopped):
                from langchain_core.runnables import RunnableLambda
                RunnableLambda(lambda value, config: google.verify('find', ROW, None, None, config=config)).batch(
                    [1, 2, 3], config={'max_concurrency': 3, 'metadata': {'parent_id': 'outer'}})
            self.assertEqual(transport.requests, [])
        finally:
            chat_runs._cancelled.reset(token)

    def test_cancellation_after_quota_preserves_stop_before_generate(self):
        stopped = [False]
        google = mm.Google('synthetic', quota=Mock(acquire=lambda *args: stopped.__setitem__(0, True)))
        token = chat_runs._cancelled.set(lambda: stopped[0])
        try:
            with SDKTransport() as transport, self.assertRaises(chat_runs.Stopped):
                google.verify('find', ROW, None, None, config={'callbacks': [Meter()]})
            self.assertEqual([op for op, _ in transport.requests], ['countTokens'])
        finally:
            chat_runs._cancelled.reset(token)

    def test_copied_provider_embedding_adapter_owns_new_admission(self):
        from copy import copy
        from llm.highlight_ingestion import BoundedGoogle, IngestionAttempts
        google = BoundedGoogle('synthetic')
        original = google.embeddings
        worker = copy(google)
        worker.call_attempts = {}
        worker.attempt_budget = IngestionAttempts(1)
        with SDKTransport() as transport:
            worker.embed({'text': 'fixed'})
        self.assertIsNot(worker.embeddings, original)
        self.assertIs(worker.embeddings.google, worker)
        self.assertEqual(worker.attempt_budget.counts, {'embedContent': 1})
        self.assertEqual(len(transport.requests), 1)

    def test_actual_graph_error_status_reaches_main_without_fake_class(self):
        google = mm.Google('synthetic', quota=Mock())
        def retrieve(query, candidate_limit, images):
            return {'query': query, 'candidates': [ROW], 'retrieval_only': [ROW['id']],
                    'rankings': {'video': [ROW['id']]}, 'retrieval_latency': 0}
        with tempfile.TemporaryDirectory() as root, override_settings(HIGHLIGHT_MULTIMODAL_CORPUS=root), \
             patch('llm.highlight_multimodal.Google', return_value=google), \
             patch('llm.highlight_multimodal.Specialist', return_value=Mock(retrieve=retrieve)), SequentialTransport([call(verdict('candidate', False, source_accessible=False, original_seconds=123.25, original_end_seconds=130.5))]):
            runtime = SimpleNamespace(state={}, config={}, tool_call_id='outer')
            result = sub_agents._delegate('ask_highlight', 'highlight', highlight_sub_agent.build(None, {})).func('find', runtime)
        self.assertEqual(result.status, 'error')
        self.assertIn('장면 없음으로 판단할 수 없습니다', result.text)
        self.assertIsNone(result.artifact[-2].artifact['multimodal']['no_match'])
