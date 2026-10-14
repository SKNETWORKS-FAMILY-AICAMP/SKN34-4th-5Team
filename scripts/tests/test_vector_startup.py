import copy
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import unittest
import urllib.error
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

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

    def test_drive_folder_destination_boundary(self):
        self.assertEqual(startup.drive_folder(startup.DRIVE_URL), '1PYIWVi_85-FW_Jsjz413IoAcDNxOvQqR')
        self.assertEqual(startup.drive_folder('folder_id-123'), 'folder_id-123')
        for value in ('', 'gdrive://folder', 'https://untrusted.invalid/folder',
                      'http://drive.google.com/drive/folders/id',
                      'https://user@drive.google.com/drive/folders/id',
                      startup.DRIVE_URL + '?token=secret', startup.DRIVE_URL + '/child',
                      'https://drive.google.com/file/d/id', '../escape'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                startup.drive_folder(value)

    def test_public_pull_wiring_cleanup_failures_and_cache_reuse(self):
        class DownloadError(Exception):
            pass

        class CheckoutError(Exception):
            pass

        for outcome in ('download', 'checkout', 'reuse', 'inaccessible', 'missing', 'corrupt'):
            with self.subTest(outcome=outcome):
                repo, download = MagicMock(), MagicMock()
                client = repo.return_value.__enter__.return_value
                client.pull.side_effect = ([None] if outcome == 'reuse' else
                                           [DownloadError(), ValueError('invalid cache')] if outcome == 'corrupt' else
                                           [DownloadError(), DownloadError()] if outcome == 'missing' else
                                           [CheckoutError() if outcome == 'checkout' else DownloadError(), None])
                seen = []

                def fetch(**kwargs):
                    root = Path(kwargs['output'])
                    self.assertTrue(root.is_dir())
                    self.assertFalse(kwargs['output'].endswith('/'))
                    shard = root / 'files/md5/ab'
                    shard.mkdir(parents=True)
                    (shard / 'tiny-object').write_bytes(b'explicit transport fixture')
                    seen.append(root)
                    if outcome == 'inaccessible':
                        raise OSError('private source')
                    return [str(shard / 'tiny-object')]

                download.side_effect = fetch
                modules = {'dvc.repo': SimpleNamespace(Repo=repo),
                           'dvc.exceptions': SimpleNamespace(DownloadError=DownloadError, CheckoutError=CheckoutError),
                           'gdown': SimpleNamespace(download_folder=download)}
                with patch.dict(sys.modules, modules), \
                        patch.dict(startup.os.environ, {'VECTOR_GDRIVE_URL': 'approved_folder'}), \
                        patch.object(Path, 'read_text', side_effect=AssertionError('No auth file reads')):
                    if outcome in ('inaccessible', 'missing', 'corrupt'):
                        with self.assertRaises((OSError, DownloadError, ValueError)):
                            startup.pull(self.root / 'work')
                    else:
                        startup.pull(self.root / 'work')
                config = repo.call_args.kwargs['config']
                temp = config['remote']['highlight-public']['url']
                self.assertFalse(Path(temp).exists())
                self.assertEqual(config['remote'], {'highlight-public': {'url': temp}})
                self.assertEqual(config['cache'], {'type': 'copy'})
                expected = {'targets': [str(self.root.resolve() / 'work/data/artifacts' / (name + '.dvc'))
                                        for name in startup.ARTIFACTS], 'remote': 'highlight-public'}
                for call in client.pull.call_args_list:
                    self.assertEqual(call.kwargs, expected)
                if outcome == 'reuse':
                    download.assert_not_called()
                    self.assertEqual(client.pull.call_count, 1)
                else:
                    download.assert_called_once_with(id='approved_folder', output=temp, quiet=True,
                                                     use_cookies=False, timeout=(30, 120))
                    self.assertEqual(seen, [Path(temp)])
                    self.assertEqual(client.pull.call_count, 1 if outcome == 'inaccessible' else 2)

    def test_child_timeout_cleans_transport_and_preserves_cache(self):
        cache = self.root / 'cache'
        cache.mkdir()
        (cache / 'startup-owner.json').write_text('{"version": 1}')
        (cache / 'work/.dvc').mkdir(parents=True)
        transports = []

        def timeout(command, **kwargs):
            transport = Path(kwargs['env']['TMPDIR'])
            (transport / 'partial').write_bytes(b'partial download')
            transports.append(transport)
            self.assertEqual(kwargs['timeout'], 3600)
            self.assertEqual(kwargs['env']['VECTOR_GDRIVE_URL'], 'approved_folder')
            self.assertNotIn('QDRANT_API_KEY', kwargs['env'])
            raise subprocess.TimeoutExpired(command, kwargs['timeout'])

        with patch.object(startup.corpus, 'safe_root', side_effect=[cache, self.runtime]), \
                patch.object(startup.shutil, 'copyfile'), \
                patch.object(startup.subprocess, 'run', side_effect=timeout), \
                patch.object(startup, 'prepare') as prepare, \
                patch.object(startup.sys, 'argv', ['vector_startup.py']), \
                patch.dict(startup.os.environ, {'VECTOR_GDRIVE_URL': 'approved_folder'}):
            with self.assertRaises(subprocess.TimeoutExpired):
                startup.run()
        self.assertEqual(len(transports), 1)
        self.assertFalse(transports[0].exists())
        self.assertTrue((cache / 'work/.dvc').is_dir())
        self.assertTrue(self.runtime.is_dir())
        prepare.assert_not_called()

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
        self.assertNotIn('dvc_config', overlay)
        self.assertNotIn('drive_user', overlay)
        self.assertIn('VECTOR_GDRIVE_URL: ${VECTOR_GDRIVE_URL:-' + startup.DRIVE_URL + '}', overlay)
        example = (ROOT / '.env.example').read_text()
        for name in ('VECTOR_DVC_CONFIG', 'VECTOR_DRIVE_USER'):
            self.assertNotIn(name, overlay)
            self.assertNotIn(name, example)
        self.assertIn('VECTOR_GDRIVE_URL=' + startup.DRIVE_URL, example)
        self.assertNotIn('secrets:', overlay)
        self.assertIn('.secrets/', (ROOT / '.gitignore').read_text().splitlines())
        dockerignore = (ROOT / '.dockerignore').read_text().splitlines()
        for path in ('.secrets/', '.dvc/config.local', '.env'):
            self.assertIn(path, dockerignore)
        init_ignore = (ROOT / 'docker/vector-startup.Dockerfile.dockerignore').read_text().splitlines()
        self.assertEqual(init_ignore[0], '**')
        self.assertNotIn('!.secrets', init_ignore)
        self.assertNotIn('!.secrets/drive-user.json', init_ignore)
        self.assertNotIn('!.dvc/config.local', init_ignore)
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
        self.assertIn('dvc==3.67.1 gdown==6.4.2', dockerfile)
        self.assertNotIn('dvc[gdrive]', dockerfile)
        self.assertNotIn('requirements-data.txt', dockerfile)
        self.assertIn('!data/artifacts/document-vectors.dvc', (ROOT / 'docker/vector-startup.Dockerfile.dockerignore').read_text())


if __name__ == '__main__':
    unittest.main(verbosity=2)
