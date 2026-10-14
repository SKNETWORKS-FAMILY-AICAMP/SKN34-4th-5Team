"""Synthetic-only contracts: actual SDK/DeepEval, no external network or judges."""
import copy
import importlib.util
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import unittest

SPEC = importlib.util.spec_from_file_location('highlight_case_quality', Path(__file__).with_name('highlight_case_quality.py'))
quality = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(quality)


def fixture():
    draft = {'id': '1', 'event_key': 'ninth-inning-fence-catch',
             'query': 'KIA와 롯데 경기에서 9회 외야수가 펜스 앞에서 뜬공을 잡는 장면 찾아줘',
             'answer': '외야수가 펜스 앞에서 뜬공을 잡는다', 'evidence': '9회 표시와 펜스 앞 포구가 보인다',
             'support': '화면의 팀명과 이닝 표시, 포구 동작으로 확인', 'uncertainties': [],
             'event_interval': [9, 14], 'essential_interval': [10, 12]}
    review = {'draft_id': '1', 'verdict': 'accept', 'verified_query': draft['query'],
              'evidence': '독립 문맥의 합성 검토에서 팀명/이닝/포구 확인', 'facts_supported': True,
              'specific': True, 'distinct': True, 'no_leakage': True,
              'references': [{'segment_id': 'synthetic-segment', 'original_interval': [10, 12],
                              'occurrence': 'original', 'required_evidence_fits': True, 'evidence': '창 안에 필수 포구와 표시가 함께 보임'}],
              'correction_query': '', 'correction_reason': '', 'reason': '합성 정상 대조'}
    return {'video_id': quality.VIDEO_IDS[0], 'duration': 100,
            'segments': {'synthetic-segment': {'video_id': quality.VIDEO_IDS[0], 'start': 8, 'end': 23}},
            'generation': json.dumps({'source_accessible': True, 'cases': [draft]}, ensure_ascii=False),
            'review': json.dumps({'source_accessible': True, 'cases': [review]}, ensure_ascii=False)}


class CaseQualityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = tempfile.TemporaryDirectory(prefix='highlight-case-contract-')
        quality.environment(Path(cls.root.name))
        cls.original_connect = socket.socket.connect
        cls.original_connect_ex = socket.socket.connect_ex
        cls.original_create = socket.create_connection
        def denied(*args, **kwargs):
            raise RuntimeError('Synthetic contracts attempted network')
        socket.socket.connect = denied
        socket.socket.connect_ex = denied
        socket.create_connection = denied

    @classmethod
    def tearDownClass(cls):
        socket.socket.connect = cls.original_connect
        socket.socket.connect_ex = cls.original_connect_ex
        socket.create_connection = cls.original_create
        cls.root.cleanup()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=self.root.name)
        self.addCleanup(self.temp.cleanup)
        self.out = Path(self.temp.name)
        self.item = fixture()
        self.gen = quality.parse_response(self.item['generation'], quality.GEN_SCHEMA)
        self.review = quality.parse_response(self.item['review'], quality.REVIEW_SCHEMA)

    def rows(self):
        return quality.observations(self.item['video_id'], self.item['duration'], self.item['segments'], self.gen, self.review)

    def sdk(self, response_hook=None):
        import httpx
        self.requests = []
        def handler(request):
            self.requests.append(request)
            if response_hook:
                result = response_hook(request)
                if result is not None:
                    return result
            if request.url.path.endswith(':countTokens'):
                return httpx.Response(200, json={'totalTokens': 25000})
            raw = self.item['review'] if len(self.requests) == 4 else self.item['generation']
            return httpx.Response(200, json={'candidates': [{'content': {'role': 'model', 'parts': [{'text': raw}]}, 'finishReason': 'STOP'}],
                'modelVersion': 'synthetic-gemini-version', 'usageMetadata': {'promptTokenCount': 25000, 'candidatesTokenCount': 200, 'totalTokenCount': 25200}})
        budget = quality.Budget(self.out / 'wire-ledger.jsonl')
        client, wire = quality.create_client('synthetic-key-not-real', budget, httpx.MockTransport(handler))
        self.addCleanup(wire.close)
        self.addCleanup(client.close)
        return client, budget

    def test_40th_admitted_41st_blocked_and_duplicate_no_retry(self):
        budget = quality.Budget(self.out / 'ledger.jsonl')
        for video in quality.VIDEO_IDS:
            for operation in ('generation-count', 'generation', 'review-count', 'review'):
                budget.admit(video, operation)
        self.assertEqual(budget.total, 40)
        with self.assertRaises(RuntimeError):
            budget.admit(quality.VIDEO_IDS[0], 'generation')
        records = [json.loads(line) for line in budget.path.read_text().splitlines()]
        self.assertEqual(len(records), 40)
        self.assertEqual(records[-1]['attempt'], 40)
        other = quality.Budget(self.out / 'other.jsonl')
        other.admit(quality.VIDEO_IDS[0], 'generation-count')
        with self.assertRaises(RuntimeError):
            other.admit(quality.VIDEO_IDS[0], 'generation-count')
        self.assertEqual(other.total, 1)

    def test_strict_truncated_duplicate_nonfinite_and_unknown_schema(self):
        for raw in (self.item['generation'][:-1], '{"source_accessible":true,"source_accessible":false,"cases":[]}',
                    self.item['generation'].replace('[10, 12]', '[NaN, 12]')):
            with self.assertRaises(ValueError):
                quality.parse_response(raw, quality.GEN_SCHEMA)
        self.gen['cases'][0]['images'] = []
        with self.assertRaises(ValueError):
            quality.validate_schema(self.gen, quality.GEN_SCHEMA)

    def test_images_answer_url_time_and_segment_leak_rejected(self):
        for query in ('장면 https://youtu.be/abc', '장면 00:12', '장면 12초', '장면 synthetic-segment',
                      '장면 data:image/png;base64,abc', '장면 image_url abc', '장면 ' + quality.VIDEO_IDS[0]):
            with self.subTest(query=query):
                self.gen['cases'][0]['query'] = query
                self.review['cases'][0]['verified_query'] = query
                self.assertFalse(self.rows()[0]['dimension_scores']['text_schema'])
                self.assertEqual(self.rows()[0]['status'], 'rejected')

    def test_wrong_id_bool_nonfinite_out_of_bounds_empty_sources(self):
        for bounds in ([True, 12], [10, float('nan')], [-1, 12], [10, 101], [12, 12]):
            with self.subTest(bounds=bounds):
                self.gen['cases'][0]['essential_interval'] = bounds
                self.assertFalse(self.rows()[0]['dimension_scores']['timestamp_validity'])
        self.gen = quality.parse_response(self.item['generation'], quality.GEN_SCHEMA)
        for video_id in ('wrong-id', quality.VIDEO_IDS[1]):
            self.item['segments']['synthetic-segment']['video_id'] = video_id
            self.assertFalse(self.rows()[0]['dimension_scores']['verified_corpus_references'])
        self.item['segments'] = {}
        self.assertEqual(self.rows()[0]['status'], 'rejected')
        self.review['cases'][0]['draft_id'] = 'unknown'
        with self.assertRaises(ValueError):
            self.rows()

    def test_references_need_explicit_support_and_not_overlap_only(self):
        ref = self.review['cases'][0]['references'][0]
        for key, value in (('required_evidence_fits', False), ('evidence', ''), ('segment_id', 'unknown'),
                           ('original_interval', [8, 9]), ('original_interval', [7, 12]), ('original_interval', [True, 12])):
            previous = copy.deepcopy(ref)
            ref[key] = value
            self.assertFalse(self.rows()[0]['dimension_scores']['verified_corpus_references'])
            ref.clear()
            ref.update(previous)
        self.review['cases'][0]['references'] = []
        self.assertEqual(self.rows()[0]['status'], 'rejected')

    def test_replay_reference_requires_original_support_and_valid_window(self):
        self.item['segments']['synthetic-replay'] = {'video_id': self.item['video_id'], 'start': 50, 'end': 70}
        replay = {'segment_id': 'synthetic-replay', 'original_interval': [55, 57], 'occurrence': 'replay',
                  'required_evidence_fits': True, 'evidence': '동일 선수/포구/화면 반복을 직접 확인한 합성 replay'}
        self.review['cases'][0]['references'].append(replay)
        self.assertEqual(len(self.rows()[0]['references']), 2)
        self.review['cases'][0]['references'] = [replay]
        self.assertEqual(self.rows()[0]['status'], 'rejected')

    def test_duplicate_question_event_and_replay_interval_rejected(self):
        for duplicate in ('query', 'event_key', 'interval'):
            with self.subTest(duplicate=duplicate):
                second = copy.deepcopy(self.gen['cases'][0])
                second.update(id='2', query='KIA와 롯데 경기의 8회 삼진 장면 찾아줘', event_key='eighth-inning-strikeout',
                              event_interval=[15, 20], essential_interval=[16, 18])
                if duplicate == 'interval':
                    second['essential_interval'] = [11, 13]
                    second['event_interval'] = [9, 14]
                else:
                    second[duplicate] = self.gen['cases'][0][duplicate]
                check = copy.deepcopy(self.review['cases'][0])
                check.update(draft_id='2', verified_query=second['query'])
                check['references'][0]['original_interval'] = second['essential_interval']
                self.gen['cases'] = [self.gen['cases'][0], second]
                self.review['cases'] = [self.review['cases'][0], check]
                self.assertFalse(self.rows()[1]['dimension_scores']['uniqueness'])

    def test_correction_keeps_immutable_draft_and_never_golden(self):
        before = copy.deepcopy(self.gen)
        self.review['cases'][0].update(verdict='correct', correction_query='수정 제안', correction_reason='이닝 불확실')
        row = self.rows()[0]
        self.assertEqual(self.gen, before)
        self.assertEqual(row['status'], 'rejected')
        self.assertEqual(row['refinement']['before'], before['cases'][0])
        self.assertEqual(row['refinement']['kind'], 'dataset-refinement/not-system-AB')

    def test_sdk_four_full_video_requests_model_offsets_no_images_usage(self):
        client, budget = self.sdk()
        rows, ready = quality.video_run(self.item['video_id'], {'duration': 100, 'title': 'UNTRUSTED', 'upload_date': '20990101'},
                                        self.item['segments'], client, self.out)
        self.assertTrue(ready)
        self.assertEqual(budget.total, 4)
        self.assertEqual(len(self.requests), 4)
        bodies = [json.loads(r.content) for r in self.requests]
        for i, body in enumerate(bodies):
            parts = body['contents'][0]['parts']
            self.assertEqual(len(parts), 2)
            from google.genai import types
            actual_part = types.Part.model_validate(parts[0])
            self.assertEqual(actual_part.file_data.file_uri, 'https://www.youtube.com/watch?v=' + self.item['video_id'])
            self.assertEqual(actual_part.file_data.mime_type, 'video/mp4')
            self.assertEqual(actual_part.video_metadata.start_offset, '0s')
            self.assertEqual(actual_part.video_metadata.end_offset, '100s')
            self.assertIsNone(actual_part.video_metadata.fps)
            self.assertNotIn('inlineData', json.dumps(body))
            self.assertTrue(self.requests[i].url.path.endswith('/models/' + quality.MODEL + (':countTokens' if i % 2 == 0 else ':generateContent')))
            if i % 2:
                config = body['generationConfig']
                self.assertEqual(config['temperature'], 0)
                self.assertEqual(config['maxOutputTokens'], 16384)
                self.assertEqual(config['responseMimeType'], 'application/json')
                self.assertIn('responseJsonSchema', config)
            else:
                self.assertNotIn('generationConfig', body)
        self.assertEqual(bodies[0]['contents'], bodies[1]['contents'])
        self.assertEqual(bodies[2]['contents'], bodies[3]['contents'])
        metadata = json.loads((self.out / self.item['video_id'] / 'review-operation.json').read_text())
        self.assertEqual(metadata['model_version'], 'synthetic-gemini-version')
        self.assertEqual(metadata['finish_reason'], 'STOP')
        self.assertEqual(metadata['usage_metadata']['total_token_count'], 25200)
        self.assertIsNone(metadata['cost_usd'])
        self.assertEqual(rows[0]['status'], 'model-reviewed/needs-human-review')
        with self.assertRaises(ValueError):
            quality.invoke(client, 'bad', True, '', 'generation', quality.GEN_SCHEMA, self.out)
        self.assertEqual(budget.total, 4)

    def prior_run(self):
        import httpx
        prior = self.out / 'prior'
        prior.mkdir()
        corpus, sources = self.out / 'corpus', self.out / 'sources'
        corpus.mkdir()
        sources.mkdir()
        quality.save(corpus / 'segments.json', {sid: {**row, 'id': sid} for sid, row in self.item['segments'].items()})
        quality.save(corpus / 'ingestion_complete.json', {'status': 'complete', 'index_mode': 'separate',
            'segments': 1, 'per_video': {v: {'duration': 100} for v in quality.VIDEO_IDS}})
        for v in quality.VIDEO_IDS:
            quality.save(sources / (v + '.info.json'), {'id': v, 'duration': 100})
        videos, segments = quality.snapshot(corpus, sources, prior)
        draft = json.loads(self.item['generation'])['cases'][0]
        self.item['generation'] = json.dumps({'source_accessible': True,
            'cases': [{**draft, 'id': str(i)} for i in range(1, 6)]})
        client, budget = self.sdk(lambda r: httpx.Response(200, json={'candidates': [{'content': {
            'role': 'model', 'parts': [{'text': '{'}]}, 'finishReason': 'MAX_TOKENS'}]})
            if len(self.requests) == 4 else None)
        rows, ready = quality.video_run(quality.VIDEO_IDS[0], videos[quality.VIDEO_IDS[0]], segments, client, prior)
        self.assertFalse(ready)
        (prior / 'wire-ledger.jsonl').write_bytes(budget.path.read_bytes())
        all_rows = rows + [r for v in quality.VIDEO_IDS[1:] for r in quality.observations(v, 100, {}, None, None)]
        quality.save(prior / 'observations.json', all_rows)
        quality.save(prior / 'quality-report.json', {'actual_http_attempts': 4, 'halted': False,
            'accepted': 0, 'error_rows': 5, 'remaining_not_run': list(quality.VIDEO_IDS[1:])})
        new = self.out / 'continued'
        new.mkdir()
        quality.snapshot(corpus, sources, new)
        return prior, new, videos, segments, rows

    def test_continuation_prior_four_plus_new36_preserves_failure_and_never_replays(self):
        import httpx
        from unittest.mock import patch
        prior, new, videos, segments, failed = self.prior_run()
        evidence = {str(p.relative_to(prior)): p.read_bytes() for p in prior.rglob('*') if p.is_file()}
        seed = quality.continuation(prior, new, videos, segments)
        budget = quality.Budget(new / 'wire-ledger.jsonl', seed)
        requests = []
        def handler(request):
            requests.append(request)
            if request.url.path.endswith(':countTokens'):
                return httpx.Response(200, json={'totalTokens': 10})
            prompt = json.loads(request.content)['contents'][0]['parts'][1]['text']
            raw = self.item['review'] if prompt.startswith(quality.REVIEW_PROMPT) else self.item['generation']
            return httpx.Response(200, json={'candidates': [{'content': {'role': 'model', 'parts': [{'text': raw}]}, 'finishReason': 'STOP'}]})
        client, wire = quality.create_client('synthetic-key', budget, httpx.MockTransport(handler))
        self.addCleanup(client.close)
        self.addCleanup(wire.close)
        with self.assertRaises(RuntimeError):
            quality.invoke(client, quality.VIDEO_IDS[0], 100, '', 'generation', quality.GEN_SCHEMA, new)
        self.assertEqual(requests, [])
        def export(rows, output):
            self.assertEqual(rows[:5], failed)
            return {'error_rows': 5}
        with patch.object(quality, 'export', side_effect=export):
            report = quality.run_live(videos, segments, client, budget, new, seed)
        self.assertEqual(len(requests), 36)
        self.assertEqual(report['actual_http_attempts'], 40)
        self.assertEqual(report['new_http_attempts'], 36)
        self.assertTrue(report['availability_preflight_succeeded'])
        self.assertEqual((new / 'prior-evidence' / 'wire-ledger.jsonl').read_bytes(), evidence['wire-ledger.jsonl'])
        self.assertEqual(evidence, {str(p.relative_to(prior)): p.read_bytes() for p in prior.rglob('*') if p.is_file()})

    def test_continuation_rejects_changed_malformed_unknown_duplicate_and_fatal_evidence(self):
        prior, new, videos, segments, _ = self.prior_run()
        ledger = (prior / 'wire-ledger.jsonl').read_bytes()
        settings = (prior / 'settings.json').read_bytes()
        report = (prior / 'quality-report.json').read_bytes()
        observations = (prior / 'observations.json').read_bytes()
        mutations = [
            ('wire-ledger.jsonl', b'{'),
            ('wire-ledger.jsonl', ledger.replace(b'GL6D3x_ZVxo', b'unknown')),
            ('wire-ledger.jsonl', ledger + ledger.splitlines(keepends=True)[0]),
            ('settings.json', settings.replace(b'"temperature": 0', b'"temperature": 1')),
            ('quality-report.json', report.replace(b'"halted": false', b'"halted": true')),
            ('observations.json', observations.replace(b'"status": "rejected"', b'"status": "missing"')),
            ('inputs/segments.json', b'{}'),
        ]
        for name, changed in mutations:
            path = prior / name
            original = path.read_bytes()
            path.write_bytes(changed)
            with self.subTest(name=name), self.assertRaises(ValueError):
                quality.continuation(prior, new, videos, segments)
            path.write_bytes(original)

    def test_successful_source_all_rejected_still_ready(self):
        check = json.loads(self.item['review'])
        check['cases'][0]['verdict'] = 'reject'
        self.item['review'] = json.dumps(check)
        client, budget = self.sdk()
        rows, ready = quality.video_run(self.item['video_id'], {'duration': 100}, self.item['segments'], client, self.out)
        self.assertTrue(ready)
        self.assertEqual(rows[0]['status'], 'rejected')
        self.assertEqual(budget.total, 4)

    def test_actual_sdk_retry_zero_and_fatal_halt(self):
        import httpx
        client, budget = self.sdk(lambda request: httpx.Response(403, json={'error': {'code': 403, 'message': 'DO NOT SAVE synthetic private error'}}))
        rows, ready = quality.video_run(self.item['video_id'], {'duration': 100}, self.item['segments'], client, self.out)
        self.assertFalse(ready)
        self.assertTrue(budget.halted)
        self.assertEqual(budget.total, 1)
        with self.assertRaises(RuntimeError):
            budget.admit(quality.VIDEO_IDS[1], 'generation-count')
        self.assertEqual(len(self.requests), 1)
        self.assertEqual(rows[0]['failure']['no_match'], None)
        self.assertNotIn('DO NOT SAVE', (self.out / self.item['video_id'] / 'failure.json').read_text())

    def test_actual_sdk_transient_error_not_retried_or_no_match(self):
        import httpx
        client, budget = self.sdk(lambda request: httpx.Response(503, json={'error': {'code': 503, 'message': 'synthetic unavailable'}}))
        rows, ready = quality.video_run(self.item['video_id'], {'duration': 100}, self.item['segments'], client, self.out)
        self.assertFalse(ready)
        self.assertFalse(budget.halted)
        self.assertEqual(budget.total, 1)
        self.assertEqual(len(self.requests), 1)
        self.assertTrue(all(r['failure']['no_match'] is None for r in rows))

    def test_failed_first_video_stops_remaining_and_preserves_all_slots(self):
        import httpx
        client, budget = self.sdk(lambda request: httpx.Response(401, json={'error': {'code': 401, 'message': 'synthetic'}}))
        report = quality.run_live({video: {'duration': 100} for video in quality.VIDEO_IDS}, self.item['segments'], client, budget, self.out)
        self.assertFalse(report['availability_preflight_succeeded'])
        self.assertEqual(report['actual_http_attempts'], 1)
        self.assertEqual(report['planned'], 50)
        self.assertEqual(report['accepted'], 0)
        self.assertEqual(report['missing'], 50)
        self.assertEqual(len(self.requests), 1)
        self.assertIsNone(report['dimensions']['source_access_review']['rate'])

    def test_unavailable_video_not_successful_negative_and_missing_retained(self):
        self.review['source_accessible'] = False
        rows = self.rows()
        self.assertFalse(rows[0]['dimension_scores']['source_access_review'])
        self.assertEqual(rows[0]['status'], 'rejected')
        self.assertEqual(len(rows), 5)
        self.assertEqual(sum(r['status'] == 'missing' for r in rows), 4)
        self.assertTrue(all(v is None for v in rows[1]['dimension_scores'].values()))

    def test_deepeval_real_framework_positive_false_source_time_controls(self):
        rows = [self.rows()[0]]
        self.review['source_accessible'] = False
        bad_source = self.rows()[0]
        bad_source['id'] += '-false-source'
        rows.append(bad_source)
        self.review['source_accessible'] = True
        self.gen['cases'][0]['essential_interval'] = [10, 101]
        bad_time = self.rows()[0]
        bad_time['id'] += '-false-time'
        rows.append(bad_time)
        from deepeval.config.settings import get_settings
        get_settings().CONFIDENT_API_KEY = 'synthetic-key-never-upload'
        report = quality.export(rows, self.out)
        self.assertIsNone(get_settings().CONFIDENT_API_KEY)
        self.assertEqual(report['accepted'], 1)
        self.assertEqual(report['rejected'], 2)
        actual = json.loads((self.out / 'deepeval-results.json').read_text())
        self.assertEqual(len(actual['test_results']), 3)
        by_id = {row['input']: {m['name']: m['score'] for m in row['metrics_data']} for row in actual['test_results']}
        self.assertEqual(by_id[bad_source['id']]['candidate_source_access_review'], 0)
        self.assertEqual(by_id[bad_source['id']]['candidate_direct_grounding_specificity'], 0)
        self.assertEqual(by_id[bad_time['id']]['candidate_timestamp_validity'], 0)
        self.assertTrue(all(v == 1 for v in by_id[rows[0]['id']].values()))
        dataset = json.loads((self.out / 'dataset.json').read_text())
        self.assertEqual(dataset['status'], 'model-reviewed/needs-human-review')
        self.assertEqual(len(dataset['cases']), 1)
        self.assertEqual(dataset['scope'], 'positives-only/text-only')

    def test_highlight_free_selects_this_suite_and_propagates_owned_cache(self):
        from unittest.mock import patch
        root = Path(__file__).resolve().parents[4]
        spec = importlib.util.spec_from_file_location('quality_ci', root / '.github' / 'evaluation_ci.py')
        ci = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(ci)
        output = self.out / 'ci-selection'
        with patch.object(sys, 'argv', ['evaluation_ci.py', 'highlight-free', '--output', str(output)]), \
                patch.dict(os.environ), patch.object(ci, 'execute') as execute:
            ci.main()
            self.assertIn(unittest.mock.call([ci.EVAL + 'test_highlight_case_quality.py', '-v'], offline=True), execute.call_args_list)
            self.assertEqual(os.environ['DEEPEVAL_CACHE_FOLDER'], str(output.resolve() / 'deepeval-cache'))
        workflow = (root / '.github' / 'workflows' / 'evaluation-regression.yml').read_text()
        self.assertIn('highlight-deterministic:', workflow)
        self.assertIn('evaluation_ci.py highlight-free', workflow)

    def test_offline_cli_end_to_end_and_output_reuse_blocked(self):
        source = self.out / 'synthetic.json'
        quality.save(source, [self.item])
        output = self.out / 'cli'
        launcher = Path(__file__).resolve().parents[4] / '.github' / 'evaluation_ci.py'
        command = [sys.executable, '-I', str(launcher), 'offline-exec', str(Path(quality.__file__)),
                   'offline', '--fixture', str(source), '--output', str(output)]
        result = subprocess.run(command, capture_output=True, text=True, env=os.environ.copy())
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(json.loads((output / 'quality-report.json').read_text())['planned'], 5)
        self.assertFalse((output / 'wire-ledger.jsonl').exists())
        self.assertTrue((output / 'cases.md').is_file())
        self.assertTrue((output / 'hash-manifest.json').is_file())
        again = subprocess.run(command, capture_output=True, text=True, env=os.environ.copy())
        self.assertEqual(again.returncode, 1)
        self.assertIn('FileExistsError', again.stdout)


if __name__ == '__main__':
    unittest.main()
