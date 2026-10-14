"""Frozen compiled stored-search retry contracts; no live models or source data."""
import importlib.util
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
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel

spec = importlib.util.spec_from_file_location('retry_mm', os.environ.get('HIGHLIGHT_CONTRACT_SOURCE',
    str(Path(__file__).parents[1]/'highlight_multimodal.py')))
mm = importlib.util.module_from_spec(spec); spec.loader.exec_module(mm)
spec = importlib.util.spec_from_file_location('llm.v2.agent.retry_graph', os.environ.get('HIGHLIGHT_GRAPH_SOURCE',
    str(Path(__file__).parents[1]/'v2/agent/highlight_sub_agent.py')))
graph_source = importlib.util.module_from_spec(spec); spec.loader.exec_module(graph_source)


class Selector(GenericFakeChatModel):
    bound_tools: list = []
    def _get_invocation_params(self, stop=None, **kwargs):
        return {**super()._get_invocation_params(stop=stop, **kwargs), 'max_output_tokens': 4000}
    def bind_tools(self, tools, **kwargs):
        self.bound_tools = tools
        return self


class RetryTests(unittest.TestCase):
    row = {'id': 'a', 'video_id': 'SlvDBmr4Bd8', 'start': 0., 'end': 900.134}
    def retrieval(self, query='original', empty=False):
        return {'query': query, 'candidates': [] if empty else [self.row],
                'retrieval_only': [] if empty else ['a'], 'rankings': {'video': ['a']}, 'retrieval_latency': 0}
    def output(self, match=True):
        return {'candidate_id': 'a', 'match': match, 'local_seconds': 700.25, 'evidence': 'visible play'}
    def selector(self, calls=None):
        calls = [{'name': 'search_stored_highlight_rag', 'args': {'query': 'revised'}, 'id': 'retry'}] if calls is None else calls
        return Selector(messages=iter([AIMessage('', tool_calls=calls)]))
    def run_graph(self, outputs, selector=None, retry=None, images=None, repeat=1):
        selector = selector or self.selector()
        google = Mock(last_model_version='synthetic', last_quota_wait=0, last_provider_latency=0)
        google.verify_candidates.side_effect = outputs * repeat
        specialist = Mock()
        specialist.retrieve.side_effect = [self.retrieval(), retry if retry is not None else self.retrieval('revised')] * repeat
        with tempfile.TemporaryDirectory() as root, override_settings(HIGHLIGHT_MULTIMODAL_CORPUS=root), \
             patch.dict(sys.modules, {'llm.highlight_multimodal': mm}), \
             patch.object(mm, 'Google', return_value=google), patch.object(mm, 'Specialist', return_value=specialist):
            compiled = graph_source.build(selector, {})
            for _ in range(repeat):
                result = compiled.invoke({'messages': [HumanMessage('invented')],
                    'highlight_original_text': 'original', 'highlight_images': images or []})
        return result, specialist, google, selector

    def test_recovery_uses_real_tool_and_original_question(self):
        from llm.tests.test_highlight_images import IMAGE
        result, store, google, selector = self.run_graph([ValueError('secret provider JSON'), [self.output()]], images=[IMAGE])
        self.assertTrue(all(c.kwargs['images'] == [IMAGE] for c in store.retrieve.call_args_list))
        self.assertTrue(all(c.kwargs['images'] == [IMAGE] for c in google.verify_candidates.call_args_list))
        self.assertNotIn(IMAGE['image_url']['url'].split(',')[1], str(result['messages']))
        self.assertEqual(store.retrieve.call_count, 2)
        self.assertEqual(store.retrieve.call_args.args, ('revised',))
        self.assertEqual([c.args[0] for c in google.verify_candidates.call_args_list], ['original', 'original'])
        self.assertEqual(selector.bound_tools[0].name, 'search_stored_highlight_rag')
        self.assertEqual(result['result']['status'], 'success')
        self.assertEqual(result['result']['retry_executed'], 1)
        self.assertEqual(result['messages'][-2].artifact['videos'][0]['start'], 700)
        self.assertNotIn('secret', str(result))

    def test_empty_retry_blocks_as_verified_absence(self):
        result, store, google, _ = self.run_graph([ValueError('secret')], retry=self.retrieval('revised', empty=True))
        self.assertEqual(store.retrieve.call_count, 2)
        self.assertEqual(google.verify_candidates.call_count, 1)
        self.assertEqual(result['result']['status'], 'block')
        self.assertTrue(result['result']['complete'])
        self.assertTrue(result['result']['no_match'])
        self.assertEqual(result['messages'][-2].artifact['videos'], [])

    def test_error_and_negative_retry_block_without_cards(self):
        for final in (RuntimeError('secret'), [self.output(False)]):
            with self.subTest(final=final):
                result, store, _, _ = self.run_graph([ValueError('first secret'), final])
                self.assertEqual(store.retrieve.call_count, 2)
                self.assertEqual(result['result']['status'], 'block')
                self.assertEqual(result['messages'][-2].artifact['videos'], [])
                self.assertTrue(result['result']['first_failure'])
                self.assertTrue(result['result']['block_reason'])
                self.assertNotIn('secret', str(result))
                self.assertIs(result['result']['no_match'], None if isinstance(final, Exception) else True)

    def test_duplicate_missing_unknown_invalid_calls_do_not_execute_retry(self):
        call = {'name': 'search_stored_highlight_rag', 'args': {'query': 'revised'}, 'id': 'one'}
        for calls in ([], [call, {**call, 'id': 'two'}], [{**call, 'name': 'ask_highlight'}],
                      [{**call, 'args': {'query': ''}}], [{**call, 'args': {'query': 'q', 'path': '/secret'}}]):
            with self.subTest(calls=calls):
                result, store, _, _ = self.run_graph([ValueError('failed')], self.selector(calls))
                self.assertEqual(store.retrieve.call_count, 1)
                self.assertEqual(result['result']['status'], 'block')
                self.assertEqual(result['result']['retry_executed'], 0)

        selector = Selector(messages=iter([AIMessage('', tool_calls=[call], invalid_tool_calls=[
            {'name': call['name'], 'args': '{', 'id': 'invalid', 'error': 'bad JSON'}])]))
        result, store, _, _ = self.run_graph([ValueError('failed')], selector)
        self.assertEqual(store.retrieve.call_count, 1)
        self.assertEqual(result['result']['status'], 'block')

    def test_normal_negative_never_calls_retry_model(self):
        result, store, _, selector = self.run_graph([[self.output(False)]])
        self.assertEqual(store.retrieve.call_count, 1)
        self.assertEqual(selector.bound_tools, [])
        self.assertTrue(result['result']['complete'])
        self.assertTrue(result['result']['no_match'])

    def test_budget_and_cancellation_do_not_trigger_retry(self):
        from llm.service.usage import InsufficientCredits, WalletBusy, UnsafeOutputLimit
        from llm.service.chat_runs import Stopped
        for error in (InsufficientCredits(), WalletBusy(), UnsafeOutputLimit(), Stopped()):
            with self.subTest(error=type(error).__name__), self.assertRaises(type(error)):
                self.run_graph([error])

    def test_retry_storage_error_blocks_and_selector_failure_is_private(self):
        result, store, _, _ = self.run_graph([ValueError('first')], retry=OSError('/secret/source'))
        self.assertEqual(store.retrieve.call_count, 2)
        self.assertEqual(result['result']['status'], 'block')
        self.assertEqual(result['result']['retry_executed'], 1)
        self.assertIsNone(result['result']['no_match'])
        selector = Mock()
        selector.bind_tools.return_value.invoke.side_effect = Exception('secret provider JSON')
        result, store, _, _ = self.run_graph([ValueError('first')], selector)
        self.assertEqual(store.retrieve.call_count, 1)
        self.assertEqual(result['result']['status'], 'block')
        self.assertNotIn('secret', str(result))

    def test_actual_generation_budget_error_preserves_exception(self):
        from llm.tests.test_highlight_langchain import SDKTransport
        from llm.service.usage import InsufficientCredits, WalletBusy, UnsafeOutputLimit
        for error in (InsufficientCredits(), WalletBusy(), UnsafeOutputLimit()):
            with self.subTest(error=type(error).__name__), SDKTransport() as transport, \
                 patch.object(mm.ChatGoogleGenerativeAI, 'with_structured_output') as structured:
                structured.return_value.invoke.side_effect = error
                with self.assertRaises(type(error)):
                    mm.Google('synthetic', quota=Mock()).verify_candidates('original', [self.row])
                self.assertEqual([op for op, _ in transport.requests], ['countTokens'])

    def test_provider_auth_and_token_capacity_fail_closed(self):
        for code in (401, 402, 403):
            with self.subTest(code=code), patch('httpx.Client.send', side_effect=mm.errors.APIError(code, {'error': {'status': 'PERMISSION_DENIED'}})):
                with self.assertRaises(RuntimeError):
                    mm.verify_retrieval(mm.Google('synthetic', quota=Mock()), self.retrieval())
        for code in (401, 402, 403):
            selector = Mock(); denied = RuntimeError('private provider body'); denied.status_code = code
            selector.bind_tools.return_value.invoke.side_effect = denied
            with self.subTest(selector_code=code), self.assertRaises(RuntimeError):
                self.run_graph([ValueError('first')], selector)
        with tempfile.TemporaryDirectory() as root:
            quota = mm.VerificationQuota(Path(root)/'quota.sqlite3', tpm=10, max_wait=0)
            google = mm.Google('synthetic', quota=quota)
            google.call = Mock(return_value={'totalTokens': 20})
            with self.assertRaises(RuntimeError): mm.verify_retrieval(google, self.retrieval())
        with override_settings(USAGE_MAX_CALL_OUTPUT_TOKENS=0):
            google = mm.Google('synthetic', quota=Mock()); google.call = Mock(return_value={'totalTokens': 9})
            with self.assertRaises(RuntimeError): mm.verify_retrieval(google, self.retrieval())

    def test_terminal_block_clears_cards_in_projection_history_and_done(self):
        from langchain_core.messages import ToolMessage
        from llm.v2.agent.chat_ui import highlight_videos
        from llm.serializer.message import project_history, done_payload
        result, _, _, _ = self.run_graph([ValueError('first'), RuntimeError('second')])
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

    def test_repeated_invocations_reset_bound(self):
        call = {'name': 'search_stored_highlight_rag', 'args': {'query': 'revised'}, 'id': 'retry'}
        selector = Selector(messages=iter([AIMessage('', tool_calls=[call]), AIMessage('', tool_calls=[call])]))
        result, store, _, _ = self.run_graph([ValueError('failed'), [self.output()]], selector, repeat=2)
        self.assertEqual(store.retrieve.call_count, 4)
        self.assertEqual(result['result']['retry_executed'], 1)


if __name__ == '__main__':
    unittest.main()
