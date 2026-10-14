import copy
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import unittest
import urllib.error
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


startup = load('vector_startup', ROOT / 'scripts/vector_startup.py')
fixtures = load('corpus_fixtures', ROOT / 'scripts/tests/test_highlight_corpus.py')


class FakeQdrant:
    def __init__(self):
        self.info = None
        self.uploads = 0
        self.manifest = None

    def wait(self):
        return {'version': '1.19.2'}

    def request(self, method, path, data=None, **kwargs):
        if method == 'GET':
            return None if self.info is None else {'result': copy.deepcopy(self.info)}
        if method == 'PUT':
            if self.info is not None:
                raise ValueError('Occupied')
            self.info = {'config': {'params': {'vectors': data['vectors']}, 'metadata': data['metadata']}, 'points_count': 0}
        elif method == 'PATCH':
            self.info['config']['metadata'] = data['metadata']
        else:
            raise AssertionError(method)
        return {'result': True}

    def upload(self, path, snapshot):
        self.uploads += 1
        self.info['points_count'] = 1
        # Qdrant snapshot recovery retains TARGET collection metadata, not snapshot metadata.
        assert self.info['config']['metadata']['embedding_model'] == 'text-embedding-3-small'
        assert self.info['config']['metadata']['dimension'] == 1536


class StartupTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.CorpusTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.root = self.fixture.root
        self.artifacts = self.root / 'artifacts'
        self.artifacts.mkdir()
        # Real helper and manifest/SQLite paths, tiny fixture rather than production data.
        meta = {'collections': {'highlight_multimodal_experiment_v1': {'vectors': {
            name: {'size': 768, 'distance': 'Cosine'} for name in ('video', 'audio', 'text')}}}}
        (self.fixture.source / 'qdrant/meta.json').write_text(json.dumps(meta))
        store = self.fixture.source / 'qdrant/collection/highlight_multimodal_experiment_v1'
        store.mkdir(parents=True)
        (self.fixture.source / 'qdrant/storage.sqlite').rename(store / 'storage.sqlite')
        marker = self.fixture.source / 'ingestion_complete.json'
        marker.write_text(json.dumps(dict(json.loads(marker.read_text()), model='gemini-embedding-2')))
        startup.corpus.transfer(self.fixture.source, self.artifacts / 'highlight-corpus')
        document = self.artifacts / 'document-vectors'
        document.mkdir()
        snapshot = document / 'documents.snapshot'
        snapshot.write_bytes(b'bounded native snapshot fixture')
        self.manifest = {'artifact_type': 'qdrant-native-collection-snapshot', 'embedding_model': 'text-embedding-3-small',
                         'dimension': 1536, 'distance': 'Cosine', 'expected_points': 1, 'qdrant_server_version': '1.19.2',
                         'snapshot_file': snapshot.name, 'snapshot_bytes': snapshot.stat().st_size,
                         'snapshot_sha256': startup.corpus.digest(snapshot)}
        self.manifest_path = document / 'manifest.json'
        self.manifest_path.write_text(json.dumps(self.manifest))
        self.runtime = self.root / 'runtime'
        self.runtime.mkdir()
        self.db = FakeQdrant()

    def prepare(self):
        return startup.prepare(self.artifacts, self.runtime, self.db, 'disposable', 'text-embedding-3-small')

    def test_first_restore_and_reuse_preserves_mutable_data(self):
        before = startup.corpus.digest(self.artifacts / 'highlight-corpus/transcripts.sqlite3')
        self.assertEqual(self.prepare(), {'documents': 'restored', 'highlights': 'restored'})
        runtime = self.runtime / 'highlight-corpus'
        (runtime / 'clips/one.mp4').write_bytes(b'legitimate app update')
        (runtime / 'qdrant/.lock').write_bytes(b'app lock')
        self.db.info['points_count'] = 2  # App additions do not require an immutable count.
        self.assertEqual(self.prepare(), {'documents': 'reused', 'highlights': 'reused'})
        self.assertEqual(self.db.uploads, 1)
        self.assertEqual(self.db.info['config']['metadata'][startup.ORIGIN], self.manifest['snapshot_sha256'])
        self.assertNotIn('vector_startup_pending', self.db.info['config']['metadata'])
        self.assertEqual((runtime / 'clips/one.mp4').read_bytes(), b'legitimate app update')
        self.assertEqual(startup.corpus.digest(self.artifacts / 'highlight-corpus/transcripts.sqlite3'), before)

    def test_missing_corrupt_and_incompatible_artifacts(self):
        snapshot = self.artifacts / 'document-vectors/documents.snapshot'
        original = snapshot.read_bytes()
        snapshot.unlink()
        with self.assertRaises(OSError):
            self.prepare()
        snapshot.write_bytes(b'bad')
        with self.assertRaises(ValueError):
            self.prepare()
        snapshot.write_bytes(original)
        self.manifest_path.write_text(json.dumps(dict(self.manifest, dimension=768)))
        with self.assertRaises(ValueError):
            self.prepare()
        self.manifest_path.write_text(json.dumps(self.manifest))
        (self.artifacts / 'highlight-corpus/clips/one.wav').write_bytes(b'bad')
        with self.assertRaises(ValueError):
            self.prepare()
        self.assertIsNone(self.db.info)
        self.assertEqual(list(self.runtime.iterdir()), [])

    def test_foreign_or_empty_document_collection_refused(self):
        for count in (0, 1):
            self.db.info = {'config': {'params': {'vectors': {'size': 1536, 'distance': 'Cosine'}},
                                      'metadata': {'embedding_model': 'text-embedding-3-small'}}, 'points_count': count}
            before = copy.deepcopy(self.db.info)
            with self.assertRaises(ValueError):
                self.prepare()
            self.assertEqual(self.db.info, before)
        self.assertEqual(self.db.uploads, 0)
        self.assertFalse((self.runtime / 'highlight-corpus').exists())

    def test_foreign_highlight_and_incomplete_owned_runtime_refused(self):
        target = self.runtime / 'highlight-corpus'
        target.mkdir()
        (target / 'foreign').write_text('keep')
        with self.assertRaises(OSError):
            self.prepare()
        self.assertIsNone(self.db.info)
        self.assertEqual((target / 'foreign').read_text(), 'keep')
        (target / 'foreign').unlink()
        target.rmdir()
        self.prepare()
        (target / 'clips/one.wav').unlink()
        with self.assertRaises(ValueError):
            self.prepare()
        self.assertEqual(self.db.uploads, 1)

    def test_symlinks_paths_overlap_and_version_fail_closed(self):
        with self.assertRaises(ValueError):
            startup.prepare(self.artifacts, self.artifacts / 'runtime', self.db, 'disposable', 'text-embedding-3-small')
        (self.artifacts / 'document-vectors/escape').symlink_to(self.root)
        with self.assertRaises(ValueError):
            self.prepare()
        (self.artifacts / 'document-vectors/escape').unlink()
        with patch.object(self.db, 'wait', return_value={'version': 'other'}):
            with self.assertRaises(ValueError):
                self.prepare()
        self.assertIsNone(self.db.info)

    def test_highlight_model_mismatch_refused(self):
        self.prepare()
        marker = self.runtime / 'highlight-corpus/ingestion_complete.json'
        marker.write_text(json.dumps(dict(json.loads(marker.read_text()), model='other-model')))
        with self.assertRaisesRegex(ValueError, 'Incompatible highlight embedding model'):
            self.prepare()
        self.assertEqual(self.db.uploads, 1)

    def test_only_404_is_missing_no_redirect_credentials(self):
        db = startup.Qdrant('http://qdrant:6333', 'never-show-key')
        for status in (401, 403, 500, 302):
            error = urllib.error.HTTPError('http://qdrant', status, 'secret-body', {}, None)
            with patch.object(db.opener, 'open', side_effect=error):
                with self.assertRaisesRegex(ValueError, '^Qdrant request failed$'):
                    db.request('GET', '/collections/test', missing=True)
        with patch.object(db.opener, 'open', side_effect=urllib.error.HTTPError('x', 404, '', {}, None)):
            self.assertIsNone(db.request('GET', '/collections/test', missing=True))
        self.assertIsNone(startup.NoRedirect().redirect_request(None, None, None, None, None, None))

    def test_cli_failure_secret_safe(self):
        result = subprocess.run([sys.executable, '-I', str(ROOT / 'scripts/vector_startup.py'), '--pull', '/missing'],
                                capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Vector startup failed:', result.stderr)
        self.assertNotIn('Traceback', result.stderr)
        self.assertNotIn('/missing', result.stderr)

    def test_ci_and_deployment_wiring(self):
        command = 'python -I .github/evaluation_ci.py offline-exec scripts/tests/test_vector_startup.py'
        self.assertIn(command, (ROOT / '.github/workflows/evaluation-regression.yml').read_text())
        overlay = (ROOT / 'docker-compose.override.yml').read_text()
        self.assertIn('condition: service_completed_successfully', overlay)
        self.assertIn('HIGHLIGHT_MULTIMODAL_CORPUS: /vector-runtime/highlight-corpus', overlay)
        for file in ('deploy-dev.yml', 'deploy-prod.yml'):
            text = (ROOT / '.github/workflows' / file).read_text()
            self.assertIn('-f docker-compose.override.yml', text)
            self.assertIn('-t dev-vector-init:latest .', text)
            self.assertIn('            dev-vector-init:latest \\', text)
            self.assertIn('--wait-timeout 4500', text)
            self.assertIn('command_timeout: 90m', text)
        dockerfile = (ROOT / 'docker/vector-startup.Dockerfile').read_text()
        self.assertNotIn('ARG ', dockerfile)
        self.assertNotIn('COPY . ', dockerfile)
        self.assertIn('!data/artifacts/document-vectors.dvc', (ROOT / 'docker/vector-startup.Dockerfile.dockerignore').read_text())


if __name__ == '__main__':
    unittest.main(verbosity=2)
