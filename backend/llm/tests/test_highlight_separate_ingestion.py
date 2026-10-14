"""Free full-transcript separate contracts; transport/media are fixed, not provider quality."""
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import httpx
from llm.tests.test_highlight_langchain import canonical_part

sys.path.insert(0, str(Path(__file__).parents[2]))
from llm import highlight_multimodal as mm
if os.environ.get('HIGHLIGHT_INGESTION_BASELINE_SOURCE'):
    spec = importlib.util.spec_from_file_location('frozen_image_only', os.environ['HIGHLIGHT_INGESTION_BASELINE_SOURCE'])
    baseline = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(baseline)
    ingestion = baseline
else:
    try:
        from llm import highlight_ingestion as ingestion
    except ImportError:
        ingestion = mm  # A image-only source has no full-transcript path; preserve its failures.


def cues_at(bounds):
    return [{'start': float(a), 'end': float(b), 'text': f'타구{i}', 'index': i}
            for i, (a, b) in enumerate(bounds)]


class SeparateIngestionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / 'source.mp4'
        self.source.write_bytes(b'original video')
        self.caption = self.root / 'source.vtt'
        self.caption.write_text('WEBVTT\n\n00:00:00.000 --> 00:00:05.000\n첫 타구\n\n00:00:05.000 --> 00:00:10.000\n다음 타구\n\n00:00:10.000 --> 00:00:15.000\n마지막 타구\n')
        self.manifest = [{'video_id': 'SlvDBmr4Bd8', 'media': str(self.source),
                          'transcript': str(self.caption), 'provenance': {'source': 'fixed original'}}]
        self.calls = []
        self.over = None
        owner = self
        def transport(req, **kwargs):
            body = json.loads(req.content)
            operation = str(req.url).rsplit(':', 1)[1]
            parts = body['contents'][0]['parts'] if operation == 'countTokens' else body['requests'][0]['content']['parts']
            self.assertEqual(len(parts), 1)
            part = canonical_part(parts[0])
            if 'inline_data' in part:
                inline = part['inline_data']
                part = {'inlineData': {'mimeType': inline['mime_type'], 'data': inline['data']}}
            mode = 'text' if 'text' in part else part['inlineData']['mimeType'].split('/')[0]
            owner.calls.append(('countTokens' if operation == 'countTokens' else 'embedContent', mode, part))
            result = {'totalTokens': 8193 if owner.over == mode else 20} if operation == 'countTokens' else {
                'embeddings': [{'values': [1.] + [0.] * 767}]}
            return httpx.Response(200, json=result, request=req)
        self.transport = transport
        def extract(src, row, folder):
            folder.mkdir(parents=True, exist_ok=True)
            paths = [folder / (row['id'] + ext) for ext in ('.mp4', '.wav')]
            for path in paths:
                path.write_bytes(b'fixed actual synchronized clip')
            return tuple(paths)
        self.extract = extract
        # Synthetic bytes have no codec duration; individual length cases override this.
        measured = patch.object(ingestion, 'duration', return_value=15.)
        measured.start()
        self.addCleanup(measured.stop)

    def reopen(self):
        opened = mm.Specialist(self.root / 'corpus', None, audit=True)
        opened.close()

    def test_shared_readback_actual_duration_bounds_are_strict(self):
        self.ingest()
        root = self.root / 'corpus'
        for mode, seconds in (('video', 14.981648), ('video', 14.999999),
                              ('audio', 14.999999), ('video', 120.000001), ('audio', 180.000001)):
            opened = mm.Specialist(root, None, audit=True)
            try:
                with patch.object(ingestion, 'duration', side_effect=lambda p: seconds if Path(p).suffix == ('.mp4' if mode == 'video' else '.wav') else 15.):
                    with self.subTest(mode=mode, seconds=seconds, entry='pending'):
                        with self.assertRaises(ValueError):
                            ingestion.validate_ingestion(opened, pending=True)
            finally:
                opened.close()
            with patch.object(ingestion, 'duration', side_effect=lambda p: seconds if Path(p).suffix == ('.mp4' if mode == 'video' else '.wav') else 15.):
                for entry, run in (('constructor', self.reopen),
                                   ('readback', lambda: ingestion.ingest_transcripts([], root, readback=True))):
                    with self.subTest(mode=mode, seconds=seconds, entry=entry):
                        with self.assertRaises(ValueError):
                            run()
        with patch.object(ingestion, 'duration', return_value=15.):
            self.reopen()
            self.assertEqual(ingestion.ingest_transcripts([], root, readback=True)['status'], 'complete')

    def test_shared_readback_row_provenance_binds_exact_per_video_hashes(self):
        self.ingest()
        root = self.root / 'corpus'
        opened = mm.Specialist(root, None, audit=True)
        sid, row = next(iter(opened.rows.items()))
        good = dict(row['provenance'])
        try:
            for field in ('caption_sha256', 'media_sha256'):
                for bad in ('0' * 64, None):
                    row['provenance'] = dict(good)
                    if bad is None:
                        row['provenance'].pop(field)
                    else:
                        row['provenance'][field] = bad
                    # Keep point/JSON equal so the source binding is the rejecting guard.
                    opened.db.overwrite_payload(mm.COLLECTION, row, points=[sid], wait=True)
                    ingestion.write_json(root / 'segments.json', opened.rows)
                    with self.subTest(field=field, missing=bad is None, entry='pending'):
                        with self.assertRaises(ValueError):
                            ingestion.validate_ingestion(opened, pending=True)
                    opened.close()
                    for entry, run in (('constructor', self.reopen),
                                       ('readback', lambda: ingestion.ingest_transcripts([], root, readback=True))):
                        with self.subTest(field=field, missing=bad is None, entry=entry):
                            with self.assertRaises(ValueError):
                                run()
                    # Restore the JSON; use the ingesting instance only to repair this fixture point.
                    row['provenance'] = dict(good)
                    ingestion.write_json(root / 'segments.json', {sid: row})
                    from qdrant_client import QdrantClient
                    db = QdrantClient(path=str(root / 'qdrant'))
                    try:
                        db.overwrite_payload(mm.COLLECTION, row, points=[sid], wait=True)
                    finally:
                        db.close()
                    opened = mm.Specialist(root, None, audit=True)
                    sid, row = next(iter(opened.rows.items()))
        finally:
            opened.close()

    def ingest(self, **kwargs):
        with patch.object(ingestion, 'duration', return_value=15.), patch.object(ingestion, 'cut', side_effect=self.extract), patch('httpx.Client.send', side_effect=self.transport):
            return ingestion.ingest_transcripts(self.manifest, self.root / 'corpus', max_calls=24,
                                                google=ingestion.BoundedGoogle(key='offline'), **kwargs)

    def test_independent_modal_counts_embeddings_named_samepoint_and_readback(self):
        result = self.ingest()
        self.assertEqual(result['status'], 'complete')
        self.assertEqual(result['call_attempts'], {'countTokens': 3, 'embedContent': 3})
        self.assertEqual([(op, mode) for op, mode, _ in self.calls],
                         [(op, mode) for mode in mm.MODALITIES for op in ('countTokens', 'embedContent')])
        self.assertEqual(self.calls[-1][2]['text'], '첫 타구 다음 타구 마지막 타구')
        opened = mm.Specialist(self.root / 'corpus', None, audit=True)
        try:
            self.assertEqual(len(opened.rows), 1)
            point = opened.db.retrieve(mm.COLLECTION, list(opened.rows), with_vectors=True)[0]
            self.assertEqual(set(point.vector), set(mm.MODALITIES))
            self.assertTrue(all(len(v) == 768 for v in point.vector.values()))
            self.assertEqual(opened.sql.execute('SELECT count(*) FROM transcript').fetchone()[0], 1)
        finally:
            opened.close()
        probes = json.loads((self.root / 'corpus/embedding_probe.json').read_text())
        self.assertEqual(probes[0]['tokens'], {m: 20 for m in mm.MODALITIES})
        with patch.object(ingestion, 'duration', return_value=15.):
            self.assertEqual(ingestion.ingest_transcripts([], self.root / 'corpus', readback=True)['segments'], 1)

    def test_each_modality_overlimit_fails_with_evidence_without_committing(self):
        for mode in mm.MODALITIES:
            with self.subTest(mode=mode):
                self.over = mode
                # Each trial is a fresh, independent corpus, never a resume or overwrite.
                folder = self.root / mode
                with patch.object(ingestion, 'duration', return_value=15.), patch.object(ingestion, 'cut', side_effect=self.extract), patch('httpx.Client.send', side_effect=self.transport):
                    with self.assertRaises(ValueError):
                        ingestion.ingest_transcripts(self.manifest, folder, max_calls=24, google=ingestion.BoundedGoogle(key='offline'))
                failure = json.loads((folder / 'ingestion_failure.json').read_text())
                self.assertEqual(failure['failures'][0]['evidence']['modality'], mode)
                self.assertEqual(failure['failures'][0]['evidence']['tokens'], 8193)
                self.assertFalse((folder / 'ingestion_complete.json').exists())
                with self.assertRaisesRegex(ValueError, 'Incomplete'):
                    mm.Specialist(folder, None)

    def test_actual_video_under15_fails_before_any_paid_http_with_length_evidence(self):
        with patch.object(ingestion, 'duration', side_effect=lambda p: 14.981648 if Path(p).suffix == '.mp4' and Path(p).name != 'source.mp4' else 15.), \
             patch.object(ingestion, 'cut', side_effect=self.extract), patch('httpx.Client.send') as network:
            with self.assertRaises(ValueError):
                ingestion.ingest_transcripts(self.manifest, self.root / 'corpus', max_calls=24,
                                              google=ingestion.BoundedGoogle(key='offline'))
            network.assert_not_called()
        evidence = json.loads((self.root / 'corpus/ingestion_failure.json').read_text())['failures'][0]['evidence']
        self.assertEqual(evidence['modality'], 'video')
        self.assertEqual(evidence['actual_duration'], 14.981648)
        self.assertEqual(evidence['minimum'], 15)
        self.assertFalse((self.root / 'corpus/ingestion_complete.json').exists())

    def test_under15_reextracts_only_next_real_cue_end_keeps_progress_and_raw_text(self):
        cues = cues_at([(i, i + 5) for i in range(0, 41, 5)])
        rows = ingestion.transcript_segments('SlvDBmr4Bd8', cues, 45)
        spans = {}
        def extract(src, row, folder):
            video, audio = self.extract(src, row, folder)
            spans[video.name] = 14.981648 if row['end'] == 15 else row['end'] - row['start']
            spans[audio.name] = row['end'] - row['start']
            return video, audio
        plan = {'media': str(self.source), 'rows': rows, 'cues': cues}
        with patch.object(ingestion, 'cut', side_effect=extract), patch.object(ingestion, 'duration', side_effect=lambda p: spans[p.name]):
            row, _, _, lengths = ingestion.extract_row(plan, rows[0], self.root / 'clips')
        self.assertEqual(row['start'], 0.)
        self.assertEqual(row['end'], 20.)
        self.assertEqual(lengths, {'video': 20., 'audio': 20.})
        self.assertEqual(row['cues'], cues[:4])
        self.assertEqual(row['text'], '타구0 타구1 타구2 타구3')
        self.assertLess(row['end'], rows[1]['end'])
        self.assertGreaterEqual(row['end'] - rows[1]['start'], 5)
        self.assertEqual(row['extraction_adjustment']['planned_id'], rows[0]['id'])
        self.assertNotEqual(row['id'], rows[0]['id'])

    def test_dryrun_nominal_six_worst24_and_no_paid_calls(self):
        with patch.object(ingestion, 'duration', return_value=15.), patch('httpx.Client.send') as network:
            report = ingestion.ingest_transcripts(self.manifest, self.root / 'unused', dry_run=True)
            self.assertEqual(report['count_tokens_calls'], 3)
            self.assertEqual(report['embedding_requests'], 3)
            self.assertEqual(report['max_provider_attempts'], 24)
            self.assertEqual(report['workers'], 8)
            self.assertIsNone(report['cost_usd'])
            self.assertFalse((self.root / 'unused').exists())
            network.assert_not_called()

    def test_explicit_cap_freshpath_and_invalid_workers_before_calls(self):
        with patch.object(ingestion, 'duration', return_value=15.), patch('httpx.Client.send') as network:
            for cap in (None, 5, True):
                with self.assertRaises(ValueError):
                    ingestion.ingest_transcripts(self.manifest, self.root / 'unused', max_calls=cap)
            for workers in (0, 9, True):
                with self.assertRaises(ValueError):
                    ingestion.ingest_transcripts(self.manifest, self.root / 'unused', dry_run=True, workers=workers)
            with self.assertRaises(ValueError):
                ingestion.ingest_transcripts(self.manifest, self.root, max_calls=24)
            network.assert_not_called()

    def test_pending_forged_completion_probe_and_hash_failclosed(self):
        self.ingest()
        root = self.root / 'corpus'
        complete = root / 'ingestion_complete.json'
        good = complete.read_text()
        complete.unlink()
        with self.assertRaisesRegex(ValueError, 'Incomplete'):
            mm.Specialist(root, None, audit=True)
        report = json.loads(good)
        report['segments'] = 2
        complete.write_text(json.dumps(report))
        with self.assertRaises(ValueError):
            mm.Specialist(root, None, audit=True)
        complete.write_text(good)
        ledger = root / 'ingestion_attempts.json'
        original_ledger = ledger.read_text()
        ledger.write_text(json.dumps({'countTokens': 3, 'embedContent': 30}))
        with self.assertRaises(ValueError):
            mm.Specialist(root, None, audit=True)
        ledger.write_text(original_ledger)
        probes = root / 'embedding_probe.json'
        original = probes.read_text()
        changed = json.loads(original)
        changed[0]['tokens']['audio'] = 8193
        probes.write_text(json.dumps(changed))
        with self.assertRaises(ValueError):
            mm.Specialist(root, None, audit=True)
        probes.write_text(original)
        next((root / 'clips').glob('*.wav')).write_bytes(b'modified audio')
        with self.assertRaises(ValueError):
            mm.Specialist(root, None, audit=True)

    def test_true_cue_policy_min15_overlap5_tail_and_raw_provenance(self):
        rows = ingestion.transcript_segments('SlvDBmr4Bd8', cues_at([(i, i + 5) for i in range(0, 41, 5)]), 45)
        self.assertEqual([(r['start'], r['end']) for r in rows], [(0., 15.), (10., 25.), (20., 35.), (30., 45.)])
        self.assertEqual({c['index'] for r in rows for c in r['cues']}, set(range(9)))
        tail = ingestion.transcript_segments('SlvDBmr4Bd8', cues_at([(0, 25), (15, 19), (20, 30)]), 30)
        self.assertEqual([(r['start'], r['end']) for r in tail], [(0., 25.), (15., 30.)])
        with self.assertRaises(ValueError):
            ingestion.transcript_segments('SlvDBmr4Bd8', cues_at([(0, 120), (110, 114), (115, 125)]), 125)
        for cues, seconds in (([(0, 14.99)], 15), ([(0, 1), (130, 145)], 150), ([(0, 121)], 122)):
            with self.assertRaises(ValueError):
                ingestion.transcript_segments('SlvDBmr4Bd8', cues_at(cues), seconds)
        parsed = ingestion.transcript(self.caption, 15)
        self.assertIn('raw', parsed[0])
        self.assertEqual(parsed[0]['source_end'], 5.)

    def test_max25_planner_looks_ahead_across_long_cue_without_losing_tail(self):
        cues = cues_at([(0, 5), (5, 10), (10, 15), (15, 20), (20, 36), (36, 40), (40, 45)])
        rows = ingestion.transcript_segments('SlvDBmr4Bd8', cues, 45)
        self.assertEqual([(r['start'], r['end']) for r in rows], [(0., 20.), (15., 36.), (20., 45.)])
        self.assertEqual({c['index'] for r in rows for c in r['cues']}, set(range(len(cues))))
        for before, after in zip(rows, rows[1:]):
            self.assertLess(before['start'], after['start'])
            self.assertLess(before['end'], after['end'])
            self.assertGreaterEqual(before['end'] - after['start'], 5)
        self.assertTrue(all(15 <= r['end'] - r['start'] <= 25 for r in rows))
        with self.assertRaises(ValueError):
            ingestion.transcript_segments('SlvDBmr4Bd8', cues_at([(0, 15), (10, 40)]), 40)

    def test_under15_next_cue_adjustment_cannot_exceed25(self):
        cues = cues_at([(0, 15), (10, 26), (20, 40)])
        row = {'video_id': 'SlvDBmr4Bd8', 'start': 0., 'end': 15.}
        ingestion.describe_row(row, cues)
        plan = {'media': str(self.source), 'rows': [row, {'start': 10., 'end': 40.}], 'cues': cues}
        with patch.object(ingestion, 'cut', side_effect=self.extract) as extract, \
             patch.object(ingestion, 'duration', side_effect=[14.981648, 15., 26., 26.]):
            with self.assertRaises(ValueError):
                ingestion.extract_row(plan, row, self.root / 'clips')
        self.assertEqual(extract.call_count, 1)

    def test_actual_over25_rejected_before_http_and_on_readback(self):
        cues = cues_at([(0, 25)])
        row = {'video_id': 'SlvDBmr4Bd8', 'start': 0., 'end': 25.}
        ingestion.describe_row(row, cues)
        plan = {'video_id': row['video_id'], 'media': str(self.source), 'transcript': str(self.caption),
                'duration': 25., 'cue_count': 1, 'cues': cues, 'rows': [row],
                'caption_sha256': 'a' * 64, 'media_sha256': 'b' * 64,
                'uncovered_intervals': [], 'provenance': {'source': 'fixed'}}
        for mode in ('video', 'audio'):
            with self.subTest(mode=mode), patch.object(ingestion, 'transcript_plan', return_value=[plan]), \
                 patch.object(ingestion, 'cut', side_effect=self.extract), \
                 patch.object(ingestion, 'duration', side_effect=lambda p: 25.01 if Path(p).suffix == ('.mp4' if mode == 'video' else '.wav') else 25.), \
                 patch('httpx.Client.send', side_effect=self.transport) as network:
                with self.assertRaises(ValueError):
                    ingestion.ingest_transcripts(self.manifest, self.root / mode, max_calls=24,
                                                  google=ingestion.BoundedGoogle(key='offline'))
                network.assert_not_called()
        with patch.object(ingestion, 'transcript_plan', return_value=[plan]), \
             patch.object(ingestion, 'duration', return_value=25.), \
             patch.object(ingestion, 'cut', side_effect=self.extract), patch('httpx.Client.send', side_effect=self.transport):
            ingestion.ingest_transcripts(self.manifest, self.root / 'corpus', max_calls=24,
                                          google=ingestion.BoundedGoogle(key='offline'))
            self.reopen()
            for mode in ('video', 'audio'):
                with patch.object(ingestion, 'duration', side_effect=lambda p: 25.01 if Path(p).suffix == ('.mp4' if mode == 'video' else '.wav') else 25.):
                    with self.subTest(mode=mode, entry='readback'), self.assertRaises(ValueError):
                        self.reopen()

    def test_shared_server_commands_are_wired(self):
        backend = (Path(os.environ['HIGHLIGHT_INGESTION_BASELINE_SOURCE']).parents[1]
                   if os.environ.get('HIGHLIGHT_INGESTION_BASELINE_SOURCE') else Path(__file__).parents[2])
        for name in ('collect_highlights', 'index_highlights'):
            command = (backend / f'llm/management/commands/{name}.py').read_text()
            self.assertIn('add_ingestion_arguments', command)
            self.assertIn('run_ingestion_command', command)
        ci = (backend.parent / '.github/evaluation_ci.py').read_text()
        self.assertIn('test_highlight_separate_ingestion.py', ci)

    def test_bounded_eight_workers_shared_ledger_and_single_db_writer(self):
        from threading import get_ident, Lock
        from qdrant_client import QdrantClient
        self.caption.write_text('WEBVTT\n\n' + '\n\n'.join(
            f'00:00:{i:02d}.000 --> 00:00:{i + 5:02d}.000\n타구{i}' for i in range(0, 41, 5)))
        writer_thread, writes, active = get_ident(), [], set()
        gate = Lock()
        original = QdrantClient.upsert
        def store(client, *args, **kwargs):
            writes.append(get_ident())
            return original(client, *args, **kwargs)
        def transport(*args, **kwargs):
            with gate:
                active.add(get_ident())
            return self.transport(*args, **kwargs)
        with patch.object(ingestion, 'duration', side_effect=lambda p: 45. if Path(p).resolve() == self.source.resolve() else 15.), \
             patch.object(ingestion, 'cut', side_effect=self.extract), patch('httpx.Client.send', side_effect=transport), \
             patch.object(QdrantClient, 'upsert', new=store):
            report = ingestion.ingest_transcripts(self.manifest, self.root / 'corpus', workers=8, max_calls=24,
                                                  google=ingestion.BoundedGoogle(key='offline'))
        self.assertEqual(report['segments'], 4)
        self.assertEqual(report['call_attempts'], {'countTokens': 12, 'embedContent': 12})
        self.assertEqual(writes, [writer_thread] * 4)
        self.assertLessEqual(len(active), 8)
        self.assertEqual(json.loads((self.root / 'corpus/ingestion_attempts.json').read_text()), report['call_attempts'])

    def test_sdk_http_retry_headers_info_and_attempts_are_accounted(self):
        from unittest.mock import Mock
        for status in (429, 503):
            provider = ingestion.BoundedGoogle(key='synthetic')
            provider.call_attempts = {}
            provider.attempt_budget = ingestion.IngestionAttempts(20)
            provider.attempt_budget.pause = Mock()
            provider.attempt_budget.cooldown = Mock()
            requests = []
            def send(request, **kwargs):
                requests.append(request)
                if len(requests) < 4:
                    return httpx.Response(status, headers={'Retry-After': '2'}, request=request, json={'error': {
                        'status': 'RESOURCE_EXHAUSTED', 'message': 'synthetic-private-do-not-expose',
                        'details': [{'@type': 'type.googleapis.com/google.rpc.RetryInfo', 'retryDelay': '3s'}]}})
                return httpx.Response(200, request=request, json={'totalTokens': 8192})
            with patch('httpx.Client.send', side_effect=send):
                self.assertEqual(provider.call('gemini-embedding-2', 'countTokens', {'contents': [{'parts': [{'text': 'fixed'}]}]}), {'totalTokens': 8192})
            self.assertEqual(len(requests), 4)
            self.assertEqual(provider.attempt_budget.counts, {'countTokens': 4})
            self.assertEqual([call.args[0] for call in provider.attempt_budget.pause.call_args_list], [3.] * 3)
            self.assertEqual(provider.attempt_budget.cooldown.call_count, 3 if status == 429 else 0)

    def test_transient_max_three_retries_nontransient_zero_and_cap_before_http(self):
        import io
        import urllib.error
        for status, cap, attempts in ((503, 24, 4), (429, 24, 4), (400, 24, 1), (503, 3, 3)):
            with self.subTest(status=status, cap=cap):
                provider = ingestion.BoundedGoogle(key='offline')
                provider.call_attempts = {}
                provider.attempt_budget = ingestion.IngestionAttempts(cap)
                error = ingestion.errors.APIError(status, {'error': {'status': 'UNAVAILABLE'}})
                with patch('httpx.Client.send', side_effect=error) as network, \
                     patch.object(provider.attempt_budget, 'pause'), patch.object(provider.attempt_budget, 'cooldown'):
                    with self.assertRaises(RuntimeError):
                        provider.call('gemini-embedding-2', 'countTokens', {'contents': [{'parts': [{'text': 'fixed'}]}]})
                self.assertEqual(network.call_count, attempts)
                self.assertEqual(sum(provider.attempt_budget.counts.values()), attempts)
        budget = ingestion.IngestionAttempts(20, clock=lambda: 0.)
        for _ in range(8):
            budget.acquire('countTokens')
        self.assertEqual(budget.tokens, 0.)
        with patch.object(budget, 'pause', side_effect=RuntimeError('burst bounded')) as pause:
            with self.assertRaises(RuntimeError):
                budget.acquire('embedContent')
            self.assertAlmostEqual(pause.call_args.args[0], .02)
        self.assertEqual(budget.counts, {'countTokens': 8})

    def test_real_framework_max25_fixed_cue_contract(self):
        from deepeval.metrics import BaseMetric
        from deepeval.test_case import LLMTestCase
        from ragas.metrics import ExactMatch
        from ragas import SingleTurnSample
        class ExactSpans(BaseMetric):
            threshold = 1.
            def measure(self, case, *args, **kwargs):
                self.score = float(case.actual_output == case.expected_output)
                self.success = self.score == self.threshold
                return self.score
            async def a_measure(self, case, *args, **kwargs):
                return self.measure(case)
            def is_successful(self):
                return self.success
            @property
            def __name__(self):
                return '15–25 actual cue exact contract'
        rows = ingestion.transcript_segments('SlvDBmr4Bd8', cues_at([(0, 5), (5, 10), (10, 15), (15, 20), (20, 36), (36, 40), (40, 45)]), 45)
        actual = json.dumps([(r['start'], r['end']) for r in rows])
        expected = json.dumps([(0., 20.), (15., 36.), (20., 45.)])
        deep = ExactSpans().measure(LLMTestCase(input='frozen cue boundaries', actual_output=actual, expected_output=expected))
        ragas = ExactMatch().single_turn_score(SingleTurnSample(response=actual, reference=expected))
        print(f'MAX25_FRAMEWORK actual={actual} expected={expected} DeepEval={deep} Ragas={ragas}')
        self.assertEqual((deep, ragas), (1., 1.))

    def test_real_deepeval_and_ragas_exact_separate_contract(self):
        from deepeval.metrics import BaseMetric
        from deepeval.test_case import LLMTestCase
        from ragas.metrics import ExactMatch
        from ragas import SingleTurnSample
        class ExactModalities(BaseMetric):
            threshold = 1.
            def measure(self, case, *args, **kwargs):
                self.score = float(case.actual_output == case.expected_output)
                self.success = self.score == self.threshold
                return self.score
            async def a_measure(self, case, *args, **kwargs):
                return self.measure(case)
            def is_successful(self):
                return self.success
            @property
            def __name__(self):
                return 'Separate ingestion exact transport'
        try:
            self.ingest()
        except AttributeError:
            if not os.environ.get('HIGHLIGHT_INGESTION_BASELINE_SOURCE'):
                raise
            # Unsupported A leaves actual transport empty; score the observed failure, not a fabricated pass.
        result = json.dumps([(op, mode) for op, mode, _ in self.calls])
        label = json.dumps([(op, mode) for mode in ('video', 'audio', 'text') for op in ('countTokens', 'embedContent')])
        deep = ExactModalities().measure(LLMTestCase(input='fixed row', expected_output=label, actual_output=result))
        ragas = ExactMatch().single_turn_score(SingleTurnSample(response=result, reference=label))
        print(f'FRAMEWORK actual={result} expected={label} DeepEval={deep} Ragas={ragas}')
        self.assertEqual((deep, ragas), (1., 1.))
        self.assertEqual(ExactModalities().measure(LLMTestCase(input='fixed row', expected_output=label, actual_output='joint merged input')), 0.)
        self.assertEqual(ExactMatch().single_turn_score(SingleTurnSample(response='joint merged input', reference=label)), 0.)


if __name__ == '__main__':
    unittest.main()
