"""Frozen sequential Gemini contracts; actual SDK/graph, synthetic HTTP, no live calls."""
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
import django
django.setup()
from django.test import override_settings
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from llm.tests.test_highlight_langchain import SDKTransport, Trace, canonical_part
from llm.tests.test_highlight_images import IMAGE

spec = importlib.util.spec_from_file_location('retry_mm', os.environ.get('HIGHLIGHT_CONTRACT_SOURCE',
    str(Path(__file__).parents[1] / 'highlight_multimodal.py')))
mm = importlib.util.module_from_spec(spec); spec.loader.exec_module(mm)
spec = importlib.util.spec_from_file_location('llm.v2.agent.retry_graph', os.environ.get('HIGHLIGHT_GRAPH_SOURCE',
    str(Path(__file__).parents[1] / 'v2/agent/highlight_sub_agent.py')))
graph_source = importlib.util.module_from_spec(spec); spec.loader.exec_module(graph_source)

ROWS = [{'id': sid, 'video_id': vid, 'start': 0., 'end': 900.134,
         'hint': {'start': 120., 'end': 140.}}
        for sid, vid in zip('abc', ('SlvDBmr4Bd8', 'abcdefghijk', '12345678901'))]


def verdict(sid='a', match=True, next_id=None, **changes):
    return {'candidate_id': sid, 'match': match, 'original_seconds': 700.25,
            'original_end_seconds': 708.5, 'source_accessible': True,
            'evidence': 'visible play outside retrieval hint', 'next_candidate_id': next_id, **changes}


def call(args, name='review_highlight_candidate'):
    return {'functionCall': {'name': name, 'args': args}}


class SequentialTransport(SDKTransport):
    def __init__(self, parts):
        super().__init__()
        self.parts = iter(parts)
    def send(self, request, **kwargs):
        if str(request.url).endswith(':generateContent'):
            part = next(self.parts)
            if isinstance(part, Exception):
                raise part
            self.response = {'modelVersion': 'synthetic-version', 'candidates': [{'content': {
                'role': 'model', 'parts': part if isinstance(part, list) else [part]}, 'finishReason': 'STOP'}],
                'usageMetadata': {'promptTokenCount': 9, 'candidatesTokenCount': 4, 'totalTokenCount': 13}}
        return super().send(request, **kwargs)


class RetryTests(unittest.TestCase):
    def run_graph(self, parts, rows=None, images=None, repeat=1, quota=None):
        rows = ROWS if rows is None else rows
        google = mm.Google('synthetic', quota=quota or Mock())
        store = Mock()
        store.retrieve.return_value = {'query': 'original', 'candidates': rows,
            'retrieval_only': [r['id'] for r in rows], 'rankings': {'video': [r['id'] for r in rows]},
            'retrieval_latency': 0}
        trace = Trace()
        with tempfile.TemporaryDirectory() as root, override_settings(HIGHLIGHT_MULTIMODAL_CORPUS=root), \
             patch.dict(sys.modules, {'llm.highlight_multimodal': mm}), \
             patch.object(mm, 'Google', return_value=google), patch.object(mm, 'Specialist', return_value=store), \
             SequentialTransport(parts * repeat) as transport:
            unrelated_model = Mock()
            compiled = graph_source.build(unrelated_model, {})
            for _ in range(repeat):
                result = compiled.invoke({'messages': [HumanMessage('invented delegation')],
                    'highlight_original_text': 'original', 'highlight_images': images or []},
                    config={'callbacks': [trace], 'metadata': {'parent_id': 'outer'}})
            unrelated_model.bind_tools.assert_not_called()
        return result, store, transport, trace

    def video_ids(self, transport):
        return [[canonical_part(p)['file_data']['file_uri'].split('=')[-1]
                 for content in body['contents'] for p in content['parts'] if 'fileData' in p]
                for op, body in transport.requests if op == 'generateContent']

    def test_rank1_stops_and_keeps_three_candidates(self):
        result, store, transport, trace = self.run_graph([call(verdict())], images=[IMAGE])
        self.assertEqual(store.retrieve.call_count, 1)
        self.assertEqual(result['result']['retrieval_only'], list('abc'))
        self.assertEqual(self.video_ids(transport), [[ROWS[0]['video_id']]])
        self.assertEqual([op for op, _ in transport.requests], ['countTokens', 'generateContent'])
        self.assertEqual(result['result']['matches'][0]['playback_seconds'], 700.25)
        self.assertEqual(result['result']['matches'][0]['playback_end_seconds'], 708.5)
        self.assertEqual(result['messages'][-2].artifact['videos'][0]['start'], 700)
        self.assertEqual(len(trace.starts), 1)
        self.assertEqual(trace.starts[0]['invocation_params']['_type'], 'chat-google-generative-ai')
        self.assertEqual(trace.starts[0]['metadata']['parent_id'], 'outer')
        self.assertNotIn(IMAGE['image_url']['url'].split(',')[1], str(result['messages']) + str(result['result']))

    def test_terminal_next_candidate_id_is_nullable_on_sdk_wire(self):
        result, _, transport, _ = self.run_graph([call(verdict())])
        body = next(body for op, body in transport.requests if op == 'generateContent')
        declaration, = [declaration for tool in body['tools']
                        for declaration in tool['functionDeclarations']
                        if declaration['name'] == 'review_highlight_candidate']
        terminal = declaration['parameters']['properties']['next_candidate_id']
        self.assertEqual(terminal['type'], 'STRING')
        self.assertIs(terminal.get('nullable'), True, terminal)
        self.assertIn('next_candidate_id', declaration['parameters']['required'])
        self.assertTrue(result['result']['complete'])
        self.assertEqual(result['result']['checked_candidates'], ['a'])

    def test_terminal_string_null_and_match_continuation_are_rejected(self):
        for match, next_id in ((True, 'null'), (False, 'null'), (True, 'b')):
            with self.subTest(match=match, next_id=next_id):
                result, store, transport, _ = self.run_graph([
                    call(verdict(match=match, next_id=next_id)), {'text': 'stop'}])
                self.assertFalse(result['result']['complete'])
                self.assertIsNone(result['result']['no_match'])
                self.assertEqual(result['result']['matches'], [])
                self.assertEqual(result['result']['checked_candidates'], ['a'])
                self.assertEqual(self.video_ids(transport), [[ROWS[0]['video_id']], []])
                self.assertEqual(store.retrieve.call_count, 1)

    def test_model_selects_rank2_then_rank3_with_one_video_per_call(self):
        for winner in ('b', 'c'):
            parts = [call(verdict('a', False, 'b')), call(verdict('b', winner == 'b', 'c' if winner == 'c' else None))]
            if winner == 'c':
                parts.append(call(verdict('c')))
            with self.subTest(winner=winner):
                result, store, transport, trace = self.run_graph(parts, images=[IMAGE])
                self.assertEqual(self.video_ids(transport), [[r['video_id']] for r in ROWS[:len(parts)]])
                self.assertEqual(store.retrieve.call_count, 1)
                self.assertEqual(result['result']['matches'][0]['candidate_id'], winner)
                self.assertEqual(len(trace.starts), len(parts))
                for op, body in transport.requests:
                    self.assertIn(IMAGE['image_url']['url'].split(',')[1], str(body))
                    self.assertIn('original', str(body))
                    self.assertNotIn('invented delegation', str(body))
                self.assertIn('visible play outside retrieval hint', str(transport.requests[3][1]))

    def test_no_tool_choice_never_auto_falls_back(self):
        result, store, transport, _ = self.run_graph([call(verdict('a', False))])
        self.assertFalse(result['result']['complete'])
        self.assertIsNone(result['result']['no_match'])
        self.assertEqual(self.video_ids(transport), [[ROWS[0]['video_id']]])
        self.assertEqual(store.retrieve.call_count, 1)

    def test_normal_absence_and_inaccessible_exhaustion_differ(self):
        for accessible in (True, False):
            parts = [call(verdict('a', False, 'b', source_accessible=accessible)),
                     call(verdict('b', False, 'c')), call(verdict('c', False))]
            with self.subTest(accessible=accessible):
                result, _, transport, _ = self.run_graph(parts)
                self.assertEqual(len(self.video_ids(transport)), 3)
                self.assertEqual(result['result']['complete'], accessible)
                self.assertIs(result['result']['no_match'], True if accessible else None)
                self.assertEqual(result['messages'][-2].artifact['videos'], [])

    def test_candidate_error_allows_same_gemini_to_choose_next(self):
        parts = [{'text': 'malformed private body'},
                 call({'candidate_id': 'b'}, 'inspect_highlight_candidate'), call(verdict('b'))]
        result, store, transport, trace = self.run_graph(parts)
        self.assertEqual(self.video_ids(transport), [[ROWS[0]['video_id']], [], [ROWS[1]['video_id']]])
        self.assertTrue(result['result']['complete'])
        self.assertEqual(result['result']['matches'][0]['candidate_id'], 'b')
        self.assertEqual(store.retrieve.call_count, 1)
        self.assertTrue(all(s['invocation_params']['_type'] == 'chat-google-generative-ai' for s in trace.starts))
        self.assertNotIn('private body', str(result))

    def test_invalid_duplicate_unknown_skipped_choices_are_bounded(self):
        for part in (call(verdict('a', False, 'a')), call(verdict('a', False, 'c')),
                     call(verdict('a', False, 'unknown')), call(verdict('a', False, 'b'), 'ask_highlight'),
                     [call(verdict('a', False, 'b')), call(verdict('a', False, 'b'))]):
            with self.subTest(part=part):
                result, store, transport, _ = self.run_graph([part, {'text': 'stop'}])
                self.assertFalse(result['result']['complete'])
                self.assertEqual([v for v in self.video_ids(transport) if v], [[ROWS[0]['video_id']]])
                self.assertEqual(store.retrieve.call_count, 1)
                self.assertEqual(result['messages'][-2].artifact['videos'], [])

    def test_identity_source_time_and_image_boundaries(self):
        for defect in ({'candidate_id': 'unknown'}, {'match': 1}, {'source_accessible': False},
                       {'original_seconds': True}, {'original_end_seconds': 700.},
                       {'original_end_seconds': 901.}, {'original_end_seconds': float('nan')}):
            with self.subTest(defect=defect):
                result, _, _, _ = self.run_graph([call(verdict(**defect))], rows=ROWS[:1])
                self.assertFalse(result['result']['complete'])
                self.assertIsNone(result['result']['no_match'])
                self.assertEqual(result['messages'][-2].artifact['videos'], [])
        result, store, transport, _ = self.run_graph([], images=[IMAGE] * 7)
        self.assertFalse(result['result']['complete'])
        self.assertEqual(transport.requests, [])
        store.retrieve.assert_not_called()

    def test_quota_auth_billing_cancellation_do_not_fallback(self):
        from llm.service.chat_runs import Stopped
        from llm.service.usage import InsufficientCredits, WalletBusy, UnsafeOutputLimit
        for error in (mm.VerificationAdmissionError('limit'), Stopped(), InsufficientCredits(), WalletBusy(), UnsafeOutputLimit()):
            quota = Mock(); quota.acquire.side_effect = error
            with self.subTest(error=type(error).__name__), self.assertRaises(type(error)):
                self.run_graph([], quota=quota)
        for code in (401, 402, 403, 429):
            with self.subTest(code=code), self.assertRaises(mm.VerificationAdmissionError):
                self.run_graph([mm.errors.APIError(code, {'error': {'status': 'RESOURCE_EXHAUSTED'}})])

    def test_empty_candidates_and_repeated_invocations(self):
        result, _, transport, _ = self.run_graph([], rows=[])
        self.assertTrue(result['result']['no_match'])
        self.assertEqual(transport.requests, [])
        result, store, transport, _ = self.run_graph([call(verdict())], repeat=2)
        self.assertEqual(store.retrieve.call_count, 2)
        self.assertEqual(len(self.video_ids(transport)), 2)
        self.assertEqual(result['result']['checked_candidates'], ['a'])

    def test_terminal_block_clears_cards_in_projection_history_and_done(self):
        from llm.v2.agent.chat_ui import highlight_videos
        from llm.serializer.message import project_history, done_payload
        result, _, _, _ = self.run_graph([call(verdict('a', False))])
        records = result['messages']; terminal = records[-2]
        card = {'videoId': 'SlvDBmr4Bd8', 'start': 700, 'label': 'earlier result'}
        earlier = ToolMessage('selected', name='ask_highlight', tool_call_id='earlier', artifact=[
            ToolMessage('selected', name='select_highlight_scenes', tool_call_id='selection', artifact={'videos': [card]})])
        blocked = ToolMessage(records[-1].text, name='ask_highlight', tool_call_id='last', status=terminal.status,
            additional_kwargs=terminal.additional_kwargs, artifact=records)
        human = HumanMessage('original', id='human')
        calls = AIMessage('', id='calls', tool_calls=[
            {'name': 'ask_highlight', 'args': {'task': 'original'}, 'id': 'earlier'},
            {'name': 'ask_highlight', 'args': {'task': 'original'}, 'id': 'last'}])
        messages = [human, calls, earlier, blocked, AIMessage(blocked.content, id='answer')]
        turns = {'human': {'status': 'completed', 'answer_id': 'answer'}}
        self.assertEqual(highlight_videos([earlier, blocked]), [])
        self.assertFalse(project_history(messages, turns)[-1].get('videos'))
        self.assertFalse(done_payload(messages, turns).get('videos'))
        failed = ToolMessage('failed', name='ask_highlight', tool_call_id='failure', status='error')
        self.assertEqual(highlight_videos([earlier, failed]), [card])
        later_card = {**card, 'start': 800, 'label': 'new result'}
        later = ToolMessage('selected', name='ask_highlight', tool_call_id='later', artifact=[
            ToolMessage('selected', name='select_highlight_scenes', tool_call_id='new-selection', artifact={'videos': [later_card]})])
        self.assertEqual(highlight_videos([earlier, blocked, later]), [later_card])
        self.assertEqual(highlight_videos([earlier, terminal]), [])


if __name__ == '__main__':
    unittest.main()
