"""Frozen synthetic video-level contracts; no source corpus or provider calls."""
import importlib.util
import json
import os
from pathlib import Path
import socket
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).parents[2]))
spec = importlib.util.spec_from_file_location('pipeline_source', os.environ.get(
    'HIGHLIGHT_CONTRACT_SOURCE', str(Path(__file__).parents[1] / 'highlight_multimodal.py')))
mm = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mm)


class PipelineTests(unittest.TestCase):
    def store(self, root):
        from qdrant_client import QdrantClient, models
        root.mkdir()
        sid = '00000000-0000-0000-0000-000000000001'
        row = {'id': sid, 'video_id': 'SlvDBmr4Bd8', 'start': 120., 'end': 140., 'text': 'visible play'}
        (root/'segments.json').write_text(json.dumps({sid: row}))
        (root/'index_mode.json').write_text('"separate"')
        (root/'ingestion_complete.json').write_text(json.dumps({'status': 'complete', 'index_mode': 'separate',
            'model': 'gemini-embedding-2', 'dimensions': 768, 'segments': 1, 'videos': 1,
            'per_video': {'SlvDBmr4Bd8': {'duration': 900.134, 'segments': 1}}}))
        import sqlite3
        sql = sqlite3.connect(root/'transcripts.sqlite3')
        sql.execute('CREATE VIRTUAL TABLE transcript USING fts5(id UNINDEXED, text)')
        sql.execute('INSERT INTO transcript VALUES (?,?)', (sid, row['text']))
        sql.commit(); sql.close()
        db = QdrantClient(path=str(root/'qdrant'))
        db.create_collection(mm.COLLECTION, vectors_config={m: models.VectorParams(size=768, distance=models.Distance.COSINE) for m in mm.MODALITIES})
        db.upsert(mm.COLLECTION, [models.PointStruct(id=sid, vector={m: [1.]*768 for m in mm.MODALITIES}, payload=row)])
        db.close()
        return row

    def test_query_has_no_full_audit_and_checks_selected_payload(self):
        from qdrant_client import QdrantClient, models
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)/'store'; row = self.store(root)
            google = Mock(); google.embed.return_value = [1.]*768
            with patch.object(mm.Specialist, 'validate_corpus', side_effect=AssertionError('full audit in query')), \
                 patch.object(QdrantClient, 'scroll', side_effect=AssertionError('scroll in query')), \
                 patch.object(mm, 'duration', side_effect=AssertionError('ffprobe in query')), \
                 patch.object(mm, 'media', side_effect=AssertionError('media in query')):
                opened = mm.Specialist(root, google)
                try:
                    found = opened.retrieve('find')
                    self.assertEqual(found['retrieval_only'], [row['id']])
                    opened.db.set_payload(mm.COLLECTION, {'end': 901.}, points=[row['id']])
                    with self.assertRaises(ValueError): opened.retrieve('find')
                finally: opened.close()
            opened = mm.Specialist(root, google)
            try:
                with self.assertRaises(ValueError): opened.validate_corpus()
            finally: opened.close()

    def test_unknown_actual_point_is_not_verified_absence(self):
        from qdrant_client import models
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)/'store'; row = self.store(root)
            google = Mock(); google.embed.return_value = [1.]*768
            opened = mm.Specialist(root, google)
            try:
                sid = '00000000-0000-0000-0000-000000000099'
                opened.db.delete(mm.COLLECTION, [row['id']])
                opened.db.upsert(mm.COLLECTION, [models.PointStruct(id=sid,
                    vector={m: [1.]*768 for m in mm.MODALITIES}, payload={**row, 'id': sid})])
                with self.assertRaises(ValueError): opened.retrieve('find')
            finally: opened.close()

    def test_returned_groups_are_validated_before_selection(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)/'store'; row = self.store(root)
            google = Mock(); google.embed.return_value = [1.]*768
            opened = mm.Specialist(root, google)
            try:
                good = SimpleNamespace(id=row['video_id'], hits=[SimpleNamespace(id=row['id'], payload=row)])
                bad_groups = [
                    SimpleNamespace(id='abcdefghijk', hits=good.hits),
                    SimpleNamespace(id=row['video_id'], hits=[]),
                    SimpleNamespace(id=row['video_id'], hits=good.hits*2),
                    SimpleNamespace(id=row['video_id'], hits=[SimpleNamespace(id=row['id'], payload={**row, 'end': 901.})]),
                    SimpleNamespace(id=row['video_id'], hits=[SimpleNamespace(id='unknown', payload=row)]),
                ]
                for bad in bad_groups:
                    with self.subTest(group=bad), patch.object(opened.db, 'query_points_groups',
                            return_value=SimpleNamespace(groups=[good, bad])):
                        with self.assertRaises(ValueError): opened.retrieve('find')
                with patch.object(opened.db, 'query_points_groups', return_value=SimpleNamespace(groups=[good]*4)):
                    with self.assertRaises(ValueError): opened.retrieve('find')
                opened.rows[row['id']]['end'] = 901.
                with patch.object(opened.db, 'query_points_groups', return_value=SimpleNamespace(groups=[good])):
                    with self.assertRaises(ValueError): opened.retrieve('find')
            finally: opened.close()

    def test_query_missing_and_incomplete_never_create_store(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)/'missing'
            with self.assertRaises((ValueError, OSError)): mm.Specialist(root, Mock())
            self.assertFalse(root.exists())
            root = Path(folder)/'store'; self.store(root)
            marker = root/'ingestion_complete.json'
            report = json.loads(marker.read_text()); report['model'] = 'unknown'
            marker.write_text(json.dumps(report))
            with self.assertRaises(ValueError): mm.Specialist(root, Mock())

    def test_unique_sources_include_disjoint_hits_and_max_three(self):
        rows = {sid: {'id': sid, 'video_id': vid, 'start': start, 'end': start+20}
                for sid, vid, start in [('a','SlvDBmr4Bd8',0), ('b','SlvDBmr4Bd8',10),
                    ('c','SlvDBmr4Bd8',200), ('d','abcdefghijk',0), ('e','12345678901',0), ('f','different11',0)]}
        self.assertEqual(mm.select_candidates(['a','a','unknown','b','c','d','e','f'], rows), ['a','d','e'])
        with self.assertRaises(ValueError): mm.select_candidates(list(rows), rows, 4)

    def test_native_grouping_multiple_disjoint_segments(self):
        from qdrant_client import QdrantClient, models
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)/'store'; first = self.store(root)
            rows = {first['id']: first}
            db = QdrantClient(path=str(root/'qdrant'))
            for i, video in enumerate(('SlvDBmr4Bd8', 'SlvDBmr4Bd8', 'abcdefghijk', '12345678901', 'different11'), 2):
                sid = f'00000000-0000-0000-0000-{i:012d}'
                row = {**first, 'id': sid, 'video_id': video, 'start': i*100., 'end': i*100.+20}
                rows[sid] = row
                db.upsert(mm.COLLECTION, [models.PointStruct(id=sid, vector={m: [1.]*768 for m in mm.MODALITIES}, payload=row)])
            db.close()
            (root/'segments.json').write_text(json.dumps(rows))
            report = json.loads((root/'ingestion_complete.json').read_text())
            report.update(segments=len(rows), videos=4,
                per_video={row['video_id']: {'duration': 900.134} for row in rows.values()})
            (root/'ingestion_complete.json').write_text(json.dumps(report))
            google = Mock(); google.embed.return_value = [1.]*768
            opened = mm.Specialist(root, google)
            try:
                for limit in (1, 2, 3):
                    found = opened.retrieve('find', candidate_limit=limit)
                    self.assertEqual(len(found['candidates']), limit)
                    self.assertEqual(len({row['video_id'] for row in found['candidates']}), limit)
            finally: opened.close()

    def test_three_full_sources_one_generation_and_original_precision(self):
        from llm.tests.test_highlight_langchain import SDKTransport, IMAGE, canonical_part
        duration = 900.134123456789
        rows = [{'id': sid, 'video_id': vid, 'start': 0., 'end': duration,
                 'hint': {'start': 120., 'end': 140.}}
                for sid, vid in zip('abc', ('SlvDBmr4Bd8','abcdefghijk','12345678901'))]
        outputs = [{'candidate_id': row['id'], 'match': True, 'original_seconds': 700.25,
                    'source_accessible': True, 'evidence': 'visible play outside hint'} for row in rows]
        with SDKTransport({'decisions': outputs}) as transport:
            result = mm.Google('synthetic', quota=Mock()).verify_candidates('original request', rows, images=[IMAGE])
        self.assertEqual([op for op, _ in transport.requests], ['countTokens','generateContent'])
        count, generate = [body for _, body in transport.requests]
        self.assertEqual(count['contents'], generate['contents'])
        sources = [p for p in generate['contents'][0]['parts'] if 'fileData' in p]
        self.assertEqual(len(sources), 3)
        self.assertEqual(len({canonical_part(s)['file_data']['file_uri'] for s in sources}), 3)
        for source in sources:
            self.assertEqual(canonical_part(source)['video_metadata']['end_offset'], '900.134123457s')
        self.assertEqual(rows[0]['end'], duration)
        self.assertTrue(result[0]['duration_transport_normalized'])
        self.assertEqual(mm.decision(result[0], rows[0])['playback_seconds'], 700.25)
        for defect in ({'video_id':'https://evil.test/v'}, {'video_id':rows[0]['video_id']}, {'end':True}, {'end':float('nan')}):
            with self.subTest(defect=defect), SDKTransport({'decisions':outputs}) as transport:
                with self.assertRaises(ValueError): mm.Google('synthetic', quota=Mock()).verify_candidates('find', [rows[0], {**rows[1], **defect}])
                self.assertEqual(transport.requests, [])

    def test_retrieval_uses_trusted_duration_not_last_hint(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)/'store'; row = self.store(root)
            google = Mock(); google.embed.return_value = [1.]*768
            opened = mm.Specialist(root, google)
            try:
                result = opened.retrieve('find')
                candidate = result['candidates'][0]
                self.assertEqual((candidate['start'],candidate['end']), (0.,900.134))
                self.assertEqual(candidate['hint'], {'start':120.,'end':140.})
                opened.sources[row['video_id']]['duration'] = 130.
                with self.assertRaises(ValueError): opened.retrieve('find')
            finally: opened.close()


if __name__ == '__main__':
    unittest.main()
