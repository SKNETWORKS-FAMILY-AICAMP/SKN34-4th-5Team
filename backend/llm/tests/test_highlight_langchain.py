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
                    specialist = object.__new__(mm.Specialist)
                    specialist.root, specialist.rows = Path('/missing'), {ROW['id']: ROW}
                    specialist.google, specialist.verifier = Mock(), mm.Google('synthetic', quota=Mock())
                    specialist.google.embed.return_value = [1.] * 768
                    specialist.db, specialist.sql = Mock(), Mock()
                    specialist.db.query_points.return_value.points = [SimpleNamespace(id=ROW['id'])]
                    specialist.sql.execute.return_value = []
                    result = specialist.ask('find')
                    self.assertEqual([op for op, _ in transport.requests], ['countTokens', 'generateContent'])
                    self.assertEqual(result['complete'], not truncated)
                    self.assertEqual(result['no_match'], None if truncated else not match)
                    if truncated:
                        self.assertNotIn(transport.text, json.dumps(result))
                    else:
                        self.assertEqual(result['decisions'][0]['output']['local_seconds'], 3.125)

    def test_candidate_batch_is_real_runnable_and_ordered(self):
        from langchain_core.runnables import RunnableLambda
        specialist = object.__new__(mm.Specialist)
        specialist.root, specialist.rows = Path('/missing'), {ROW['id']: ROW}
        specialist.google, specialist.verifier = Mock(), Mock()
        specialist.google.embed.return_value = [1.] * 768
        specialist.verifier.verify.return_value = {'candidate_id': ROW['id'], 'match': True,
            'local_seconds': 3.125, 'evidence': OUTPUT['evidence']}
        specialist.db, specialist.sql = Mock(), Mock()
        specialist.db.query_points.return_value.points = [SimpleNamespace(id=ROW['id'])]
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
