"""Synthetic SDK HTTP contracts; no live Gemini or quality claims."""
import base64
import importlib.util
import json
import os
from io import BytesIO
from pathlib import Path
import urllib.request
import socket
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import httpx
from google.genai import types


def canonical_part(part):
    return types.Part.model_validate(part).model_dump(exclude_none=True)
from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.embeddings import Embeddings

sys.path.insert(0, str(Path(__file__).parents[2]))
spec = importlib.util.spec_from_file_location('migration_source', os.environ.get(
    'HIGHLIGHT_CONTRACT_SOURCE', str(Path(__file__).parents[1] / 'highlight_multimodal.py')))
mm = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mm)
ROW = {'id': 'candidate', 'video_id': 'SlvDBmr4Bd8', 'start': 120.125, 'end': 140.375}
OUTPUT = {'candidate_id': 'candidate', 'match': True, 'original_seconds': 123.25,
          'source_accessible': True, 'evidence': 'visible bat contact'}
IMAGE = {'type': 'image_url', 'image_url': {'url': 'data:image/webp;base64,' + base64.b64encode(b'original-image').decode()}}


class SDKTransport:
    """Intercept after official serializers, at httpx send, with network denied."""
    def __init__(self, output=OUTPUT):
        self.output, self.requests = output, []

    def send(self, request, **kwargs):
        body = json.loads(request.content)
        operation = str(request.url).split(':')[-1]
        self.requests.append((operation, body))
        if operation == 'countTokens':
            result = {'totalTokens': 9}
        elif operation == 'embedContent':
            result = {'embedding': {'values': [1.] * mm.DIM}}
        elif operation == 'batchEmbedContents':
            result = {'embeddings': [{'values': [1.] * mm.DIM}]}
        else:
            result = {'modelVersion': 'synthetic-version', 'candidates': [{'content': {
                'role': 'model', 'parts': [{'text': getattr(self, 'text', json.dumps(self.output))}]}, 'finishReason': 'STOP'}],
                'usageMetadata': {'promptTokenCount': 9, 'candidatesTokenCount': 4, 'totalTokenCount': 13}}
        if operation == 'generateContent' and hasattr(self, 'response'):
            result = self.response
        return httpx.Response(200, json=result, request=request)

    def __enter__(self):
        self.network = patch.object(socket.socket, 'connect', side_effect=AssertionError('network forbidden'))
        self.http = patch.object(httpx.Client, 'send', side_effect=self.send)
        self.network.start(); self.http.start()
        return self

    def __exit__(self, *args):
        self.http.stop(); self.network.stop()


class Trace(BaseCallbackHandler):
    raise_error = True
    def __init__(self):
        self.starts, self.ends = [], []
    def on_chat_model_start(self, serialized, messages, **kwargs):
        self.starts.append(kwargs)
    def on_llm_end(self, response, **kwargs):
        self.ends.append(response)


class LangChainMigrationTests(unittest.TestCase):
    def video_specialist(self, ranking):
        specialist = object.__new__(mm.Specialist)
        specialist.root = Path('/synthetic')
        specialist.rows = {sid: {'id': sid, 'video_id': f'SlvDBmr4Bd{i}',
            'start': i * 20, 'end': (i + 1) * 20} for i, sid in enumerate('zabcde')}
        specialist.google, specialist.verifier = Mock(), Mock()
        specialist.verifier.last_model_version = 'synthetic'
        specialist.google.embed.return_value = [1.] * mm.DIM
        specialist.db, specialist.sql = Mock(), Mock()
        specialist.db.query_points_groups.side_effect = lambda *args, **kwargs: SimpleNamespace(
            groups=[SimpleNamespace(id=specialist.rows[sid]['video_id'],
                hits=[SimpleNamespace(id=sid, payload=specialist.rows[sid])])
                for sid in ranking[:kwargs['limit']]])
        specialist.sql.execute.return_value = [('a',), ('b',), ('c',)]
        specialist.verifier.verify.side_effect = lambda query, row, *args, **kwargs: {
            'candidate_id': row['id'], 'match': row['id'] == 'a', 'local_seconds': 1,
            'evidence': 'synthetic visible play'}
        return specialist

    def assert_video_lookup(self, specialist, result, ranking, limit=3):
        specialist.db.query_points_groups.assert_called_once_with(mm.COLLECTION,
            query=[1.] * mm.DIM, using='video', group_by='video_id', group_size=1,
            limit=limit)
        specialist.sql.execute.assert_not_called()
        self.assertEqual(result['rankings'], {'video': ranking})

    def test_video_order_beats_cross_modal_agreement_and_preserves_images(self):
        ranking = ['z', 'a', 'b', 'c', 'd', 'e']
        specialist = self.video_specialist(ranking)
        image = {'type': 'image_url', 'image_url': {'url': IMAGE['image_url']['url'].replace('image/webp', 'image/png')}}
        result = specialist.ask('find', images=[image], config={'metadata': {'parent_id': 'parent'}})
        self.assertEqual(result['retrieval_only'], ['z', 'a', 'b'])
        self.assertEqual([d['candidate_id'] for d in result['decisions']], ['z', 'a', 'b'])
        self.assertEqual([m['candidate_id'] for m in result['matches']], ['a'])
        self.assertEqual(result['matches'][0]['playback_seconds'], 21)
        self.assertTrue(result['complete'])
        self.assertFalse(result['no_match'])
        specialist.google.embed.assert_called_once_with([{'text': 'find'}, *mm.image_parts([image])])
        for call in specialist.verifier.verify.call_args_list:
            self.assertEqual(call.args[0], 'find')
            self.assertEqual(call.kwargs['images'], [image])
            self.assertEqual(call.kwargs['config']['metadata']['parent_id'], 'parent')
        self.assertNotIn('base64', json.dumps(result))
        self.assert_video_lookup(specialist, result, ['z', 'a', 'b'])

    def test_video_duplicates_unknown_overlap_touching_and_limits(self):
        ranking = ['unknown', 'z', 'z', 'a', 'b', 'c', 'd', 'e']
        for limit, expected in ((None, ['z', 'c', 'd']), (1, ['z']), (3, ['z', 'c', 'd'])):
            with self.subTest(limit=limit):
                specialist = self.video_specialist(expected)
                specialist.rows['a'].update(video_id=specialist.rows['z']['video_id'], start=10, end=30)
                specialist.rows['b'].update(video_id=specialist.rows['z']['video_id'], start=200, end=220)
                specialist.rows['c'].update(video_id='different11', start=0, end=20)
                result = specialist.ask('find', **({} if limit is None else {'candidate_limit': limit}))
                self.assertEqual(result['retrieval_only'], expected)
                self.assertEqual([d['candidate_id'] for d in result['decisions']], expected)
                self.assertEqual(specialist.verifier.verify.call_count, len(expected))
                self.assertTrue(result['complete'])
                self.assertTrue(result['no_match'])
                self.assertEqual(mm.select_candidates(ranking, specialist.rows, limit or 3), expected)
                self.assert_video_lookup(specialist, result, expected, limit or 3)
        for limit in (0, 4, 5, 6):
            specialist = self.video_specialist(ranking)
            with self.assertRaises(ValueError):
                specialist.ask('find', candidate_limit=limit)
            specialist.google.embed.assert_not_called()
            specialist.db.query_points_groups.assert_not_called()

    def test_video_empty_negative_and_verifier_errors(self):
        for ranking, error in (([], None), (['z'], None), (['z'], ValueError('bad')),
                               (['z'], RuntimeError('unavailable'))):
            with self.subTest(ranking=ranking, error=error):
                specialist = self.video_specialist(ranking)
                if error:
                    specialist.verifier.verify.side_effect = error
                result = specialist.ask('find')
                self.assertEqual(result['retrieval_only'], ranking)
                self.assertEqual(len(result['decisions']), len(ranking))
                self.assertEqual(result['matches'], [])
                self.assertEqual(result['complete'], error is None)
                self.assertIs(result['no_match'], True if error is None else None)
                specialist.google.embed.assert_called_once_with({'text': 'task: search result | query: find'})
                self.assertEqual(specialist.verifier.verify.call_count, len(ranking))
                self.assert_video_lookup(specialist, result, ranking)

    def test_real_structured_model_serialization_callbacks(self):
        trace = Trace()
        with SDKTransport() as transport:
            google = mm.Google('synthetic', quota=Mock())
            result = google.verify('find', ROW, '/missing.mp4', '/missing.wav', images=[IMAGE],
                                   config={'callbacks': [trace], 'metadata': {'parent_id': 'parent'}})
        self.assertEqual(result['local_seconds'], 3.125)
        self.assertEqual([op for op, _ in transport.requests], ['countTokens', 'generateContent'])
        count, generate = [body for _, body in transport.requests]
        self.assertEqual(count['contents'], generate['contents'])
        source = generate['contents'][0]['parts'][1]
        self.assertEqual(canonical_part(source), canonical_part({'fileData': {'mimeType': 'video/mp4', 'fileUri': 'https://www.youtube.com/watch?v=SlvDBmr4Bd8'},
                                 'videoMetadata': {'startOffset': '120.125s', 'endOffset': '140.375s'}}))
        self.assertEqual(canonical_part(generate['contents'][0]['parts'][2]), canonical_part(mm.image_parts([IMAGE], embedding=False)[0]))
        self.assertEqual(generate['generationConfig']['maxOutputTokens'], 4000)
        self.assertEqual(generate['generationConfig']['temperature'], 0)
        self.assertEqual(len(trace.starts), 1)
        self.assertEqual(trace.starts[0]['metadata']['parent_id'], 'parent')
        self.assertEqual(trace.starts[0]['invocation_params']['max_output_tokens'], 4000)
        self.assertEqual(trace.ends[0].generations[0][0].message.usage_metadata['output_tokens'], 4)
        self.assertEqual(google.last_model_version, 'synthetic-version')

    def test_embedding_text_and_joint_preserve_unspecified_task(self):
        with SDKTransport() as transport:
            google = mm.Google('synthetic')
            self.assertIsInstance(google.embeddings, Embeddings)
            self.assertEqual(len(google.embed({'text': 'task: search result | query: find'})), 768)
            self.assertEqual(len(google.embed([{'text': 'find'}, *mm.image_parts([IMAGE], embedding=False)])), 768)
        self.assertEqual(len(transport.requests), 2)
        for operation, body in transport.requests:
            self.assertEqual(operation, 'batchEmbedContents')
            self.assertEqual(len(body['requests']), 1)
            request = body['requests'][0]
            self.assertEqual(request['outputDimensionality'], 768)
            self.assertNotIn('taskType', request)
            self.assertEqual(request['model'], 'models/gemini-embedding-2')
        self.assertEqual(len(transport.requests[1][1]['requests'][0]['content']['parts']), 2)

    def test_invalid_metadata_and_image_limit_zero_transport(self):
        with SDKTransport() as transport:
            google = mm.Google('synthetic', quota=Mock())
            for defect in ({'video_id': 'short'}, {'start': True}, {'end': float('nan')}, {'end': 120.125}):
                with self.assertRaises(ValueError):
                    google.verify('find', {**ROW, **defect}, None, None)
            for count in (7, 10):
                with self.assertRaises(ValueError):
                    google.verify('find', ROW, None, None, images=[IMAGE] * count)
        self.assertEqual(transport.requests, [])

    def test_malformed_and_inaccessible_are_not_negative(self):
        def legacy_send(request, **kwargs):
            # Reach the frozen pre-migration transport, not a denied socket exception.
            return BytesIO(transport.send(httpx.Request('POST', request.full_url, content=request.data)).content)

        for output in ({**OUTPUT, 'source_accessible': False}, {**OUTPUT, 'match': 1},
                       {**OUTPUT, 'original_seconds': ROW['end']}, {**OUTPUT, 'candidate_id': 'other'}, None):
            with self.subTest(output=output), SDKTransport(output) as transport, \
                 patch.object(urllib.request, 'urlopen', side_effect=legacy_send):
                google = mm.Google('synthetic', quota=Mock())
                with self.assertRaises((RuntimeError, ValueError)):
                    google.verify('find', ROW, None, None)
                self.assertEqual([op for op, _ in transport.requests], ['countTokens', 'generateContent'])

        for match in (False, True):
            for truncated in (True, False):
                with self.subTest(match=match, truncated=truncated), SDKTransport() as transport, \
                     patch.object(urllib.request, 'urlopen', side_effect=legacy_send):
                    transport.text = json.dumps({**OUTPUT, 'match': match})
                    if truncated:
                        transport.text = transport.text[:-1]
                    elif hasattr(mm.Google, 'review_candidate'):
                        # Current ask requires a tool verdict; frozen JSON sources keep their wire contract.
                        transport.response = {'modelVersion': 'synthetic-version', 'candidates': [{'content': {
                            'role': 'model', 'parts': [{'functionCall': {'name': 'review_highlight_candidate',
                                'args': {**OUTPUT, 'match': match, 'original_end_seconds': 124.25,
                                         'next_candidate_id': None}}}]}, 'finishReason': 'STOP'}],
                            'usageMetadata': {'promptTokenCount': 9, 'candidatesTokenCount': 4, 'totalTokenCount': 13}}
                    specialist = object.__new__(mm.Specialist)
                    specialist.root, specialist.rows = Path('/missing'), {ROW['id']: ROW}
                    specialist.google, specialist.verifier = Mock(), mm.Google('synthetic', quota=Mock())
                    specialist.google.embed.return_value = [1.] * 768
                    specialist.db, specialist.sql = Mock(), Mock()
                    specialist.db.query_points_groups.return_value.groups = [SimpleNamespace(id=ROW['video_id'], hits=[SimpleNamespace(id=ROW['id'], payload=ROW)])]
                    specialist.sources = {ROW['video_id']: {'duration': ROW['end']}}
                    specialist.sql.execute.return_value = []
                    result = specialist.ask('find')
                    self.assertEqual([op for op, _ in transport.requests], ['countTokens', 'generateContent'])
                    self.assertEqual(result['complete'], not truncated)
                    self.assertEqual(result['no_match'], None if truncated else not match)
                    if truncated:
                        self.assertNotIn(transport.text, json.dumps(result))
                    else:
                        self.assertEqual(result['decisions'][0]['output']['local_seconds'], 123.25)

    def test_retrieval_has_zero_generation_calls(self):
        specialist = self.video_specialist(['z', 'a', 'b'])
        result = specialist.retrieve('find')
        self.assertEqual([row['id'] for row in result['candidates']], ['z', 'a', 'b'])
        specialist.verifier.verify.assert_not_called()
        specialist.google.verify.assert_not_called()
        specialist.google.verify_candidates.assert_not_called()
        self.assert_video_lookup(specialist, result, ['z', 'a', 'b'])

    def test_three_candidates_one_real_gemini_generation(self):
        rows = [{**ROW, 'id': sid, 'video_id': f'SlvDBmr4Bd{i}', 'start': 0.,
                 'end': 900.134} for i, sid in enumerate('abc')]
        outputs = [{**OUTPUT, 'candidate_id': row['id'], 'original_seconds': row['start'] + 1}
                   for row in rows]
        trace = Trace()
        with SDKTransport({'decisions': outputs}) as transport:
            google = mm.Google('synthetic', quota=Mock())
            result = google.verify_candidates('original question', rows, images=[IMAGE],
                config={'callbacks': [trace], 'metadata': {'parent_id': 'parent'}})
        self.assertEqual([o['candidate_id'] for o in result], list('abc'))
        self.assertEqual([op for op, _ in transport.requests], ['countTokens', 'generateContent'])
        count, generate = [body for _, body in transport.requests]
        self.assertEqual(count['contents'], generate['contents'])
        parts = generate['contents'][0]['parts']
        self.assertIn('original question', parts[0]['text'])
        sources = [p for p in parts if 'fileData' in p]
        self.assertEqual(len(sources), 3)
        for row, source in zip(rows, sources):
            self.assertEqual(canonical_part(source), canonical_part({'fileData': {
                'mimeType': 'video/mp4', 'fileUri': 'https://www.youtube.com/watch?v=' + row['video_id']},
                'videoMetadata': {'startOffset': str(row['start']) + 's', 'endOffset': str(row['end']) + 's'}}))
        self.assertEqual(canonical_part(parts[-1]), canonical_part(mm.image_parts([IMAGE], embedding=False)[0]))
        self.assertEqual(len(trace.starts), 1)
        self.assertEqual(trace.starts[0]['metadata']['parent_id'], 'parent')
        google.quota.acquire.assert_called_once_with(google.model, 9)

    def test_group_exact_coverage_and_fail_closed_boundaries(self):
        rows = [ROW, {**ROW, 'video_id': 'abcdefghijk', 'id': 'second', 'start': 200, 'end': 220}]
        good = [OUTPUT, {**OUTPUT, 'candidate_id': 'second', 'original_seconds': 201}]
        bad = [good[:1], [good[0], good[0]], [good[0], {**good[1], 'candidate_id': 'unknown'}]]
        bad += [[good[0], {**good[1], **defect}] for defect in (
            {'source_accessible': False}, {'source_accessible': 1}, {'match': 1},
            {'original_seconds': True}, {'original_seconds': float('nan')},
            {'original_seconds': float('inf')}, {'original_seconds': 220},
            {'original_seconds': 199}, {'evidence': ''})]
        for outputs in bad:
            with self.subTest(outputs=outputs), SDKTransport({'decisions': outputs}):
                with self.assertRaises((ValueError, RuntimeError)):
                    mm.Google('synthetic', quota=Mock()).verify_candidates('find', rows)
        with SDKTransport({'decisions': good}) as transport:
            transport.text = json.dumps({'decisions': good})[:-1]
            with self.assertRaises(ValueError):
                mm.Google('synthetic', quota=Mock()).verify_candidates('find', rows)
        with SDKTransport({'decisions': list(reversed(good))}):
            result = mm.Google('synthetic', quota=Mock()).verify_candidates('find', rows)
            self.assertEqual([o['candidate_id'] for o in result], ['candidate', 'second'])
        with SDKTransport({'decisions': good}) as transport:
            with self.assertRaises(ValueError):
                mm.Google('synthetic', quota=Mock()).verify_candidates('find', [ROW, ROW])
            self.assertEqual(transport.requests, [])

    def test_candidate_batch_is_real_runnable_and_ordered(self):
        from langchain_core.runnables import RunnableLambda
        specialist = object.__new__(mm.Specialist)
        specialist.root, specialist.rows = Path('/missing'), {ROW['id']: ROW}
        specialist.google, specialist.verifier = Mock(), Mock()
        specialist.google.embed.return_value = [1.] * 768
        specialist.verifier.verify.return_value = {'candidate_id': ROW['id'], 'match': True,
            'local_seconds': 3.125, 'evidence': OUTPUT['evidence']}
        specialist.db, specialist.sql = Mock(), Mock()
        specialist.db.query_points_groups.return_value.groups = [SimpleNamespace(id=ROW['video_id'], hits=[SimpleNamespace(id=ROW['id'], payload=ROW)])]
        specialist.sql.execute.return_value = []
        original = RunnableLambda.batch
        batches = []
        def batch(runnable, inputs, config=None, **kwargs):
            batches.append((inputs, config))
            return original(runnable, inputs, config=config, **kwargs)
        with patch.object(RunnableLambda, 'batch', batch):
            result = specialist.ask('find', config={'metadata': {'parent_id': 'parent'}})
        self.assertTrue(result['complete'])
        self.assertEqual(batches[0][0], ['candidate'])
        self.assertEqual(batches[0][1]['max_concurrency'], 3)
        self.assertEqual(specialist.verifier.verify.call_args.kwargs['config']['metadata']['parent_id'], 'parent')


if __name__ == '__main__':
    unittest.main()
