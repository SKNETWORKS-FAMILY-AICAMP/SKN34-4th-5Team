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


if __name__ == '__main__':
    unittest.main()
