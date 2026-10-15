import copy
import importlib.util
import json
import re
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

    def auth_fixture(self):
        config, token = self.root / 'config.local', self.root / 'drive-user.json'
        config.write_text('private config fixture')
        token.write_text(json.dumps({'_class': 'OAuth2Credentials', '_module': 'oauth2client.client',
                                    'invalid': False, 'refresh_token': 'fixture-refresh',
                                    'token_uri': 'https://oauth2.googleapis.com/token'}))
        return config, token

    def test_authenticated_pull_selected_remote_token_override_and_cleanup(self):
        config, token = self.auth_fixture()
        before = (config.read_bytes(), token.read_bytes())
        selected = {'url': 'gdrive://approved-folder', 'gdrive_client_id': 'fixture-id',
                    'gdrive_client_secret': 'fixture-secret', 'gdrive_user_credentials_file': '/host/only',
                    'gdrive_use_service_account': True}
        for failure in (False, True):
            with self.subTest(failure=failure):
                repo, loader, credentials = MagicMock(), MagicMock(), MagicMock()
                loader.load_file.return_value = {'remote': {'highlight-drive': selected, 'other': {'url': 'bad'}},
                                                 'cache': {'dir': '/host/cache'}}
                copies = []

                def pull(**kwargs):
                    cfg = repo.call_args.kwargs['config']
                    self.assertEqual(cfg['cache'], {'type': 'copy'})
                    self.assertEqual(set(cfg['remote']), {'highlight-drive'})
                    actual = cfg['remote']['highlight-drive']
                    self.assertEqual(set(actual), {'url', 'gdrive_client_id', 'gdrive_client_secret',
                                                  'gdrive_user_credentials_file'})
                    copied = Path(actual['gdrive_user_credentials_file'])
                    copies.append(copied)
                    self.assertEqual(copied.read_bytes(), before[1])
                    self.assertEqual(copied.stat().st_mode & 0o777, 0o600)
                    copied.write_text('refreshed token')
                    self.assertEqual(kwargs, {'targets': [str(self.root.resolve() / 'work/data/artifacts' / (name + '.dvc'))
                                                         for name in startup.ARTIFACTS], 'remote': 'highlight-drive'})
                    if failure:
                        raise OSError('private remote failure')

                repo.return_value.__enter__.return_value.pull.side_effect = pull
                modules = {'dvc.repo': SimpleNamespace(Repo=repo), 'dvc.config': SimpleNamespace(Config=loader),
                           'oauth2client.client': SimpleNamespace(OAuth2Credentials=credentials)}
                with patch.dict(sys.modules, modules), patch.object(startup, 'DVC_CONFIG', str(config)), \
                        patch.object(startup, 'DRIVE_USER', str(token)):
                    if failure:
                        with self.assertRaises(OSError):
                            startup.pull(self.root / 'work')
                    else:
                        startup.pull(self.root / 'work')
                loader.load_file.assert_called_once_with(str(config))
                credentials.from_json.assert_called_once()
                self.assertFalse(copies[0].parent.exists())
                self.assertEqual((config.read_bytes(), token.read_bytes()), before)
                self.assertEqual(list(self.runtime.iterdir()), [])

    def test_missing_invalid_auth_fails_before_network(self):
        config, token = self.auth_fixture()
        valid = token.read_text()
        for problem in ('missing-config', 'invalid-config', 'missing-remote', 'local-remote', 'empty-drive',
                        'missing-token', 'malformed-token', 'client-json', 'invalid-token', 'no-refresh', 'bad-token-parser'):
            with self.subTest(problem=problem):
                token.write_text(valid)
                repo, loader, credentials = MagicMock(), MagicMock(), MagicMock()
                loader.load_file.return_value = {'remote': {'highlight-drive': {'url': 'gdrive://folder'}}}
                if problem in ('missing-config', 'invalid-config'):
                    loader.load_file.side_effect = OSError('bad config')
                elif problem == 'missing-remote':
                    loader.load_file.return_value = {}
                elif problem in ('local-remote', 'empty-drive'):
                    loader.load_file.return_value = {'remote': {'highlight-drive': {'url': '/local' if problem == 'local-remote' else 'gdrive://'}}}
                elif problem == 'missing-token':
                    token.unlink()
                elif problem == 'malformed-token':
                    token.write_text('{')
                elif problem == 'client-json':
                    token.write_text('{"installed": {}}')
                elif problem in ('invalid-token', 'no-refresh'):
                    value = json.loads(valid)
                    value['invalid' if problem == 'invalid-token' else 'refresh_token'] = True if problem == 'invalid-token' else ''
                    token.write_text(json.dumps(value))
                elif problem == 'bad-token-parser':
                    credentials.from_json.side_effect = ValueError('bad token')
                modules = {'dvc.repo': SimpleNamespace(Repo=repo), 'dvc.config': SimpleNamespace(Config=loader),
                           'oauth2client.client': SimpleNamespace(OAuth2Credentials=credentials)}
                with patch.dict(sys.modules, modules), patch.object(startup, 'DVC_CONFIG', str(config)), \
                        patch.object(startup, 'DRIVE_USER', str(token)), self.assertRaises((ValueError, OSError)):
                    startup.pull(self.root / 'work')
                repo.assert_not_called()
                self.assertEqual(list(self.runtime.iterdir()), [])

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
            self.assertEqual(set(kwargs['env']), {'PATH', 'HOME', 'DVC_NO_ANALYTICS', 'TMPDIR'})
            self.assertNotIn('QDRANT_API_KEY', kwargs['env'])
            if failure == 'timeout':
                raise subprocess.TimeoutExpired(command, kwargs['timeout'])
            raise subprocess.CalledProcessError(1, command)

        sentinel = self.runtime / 'existing-data'
        sentinel.write_bytes(b'keep runtime')
        for failure in ('timeout', 'dvc-error'):
            with self.subTest(failure=failure), \
                    patch.object(startup.corpus, 'safe_root', side_effect=[cache, self.runtime]), \
                    patch.object(startup.shutil, 'copyfile'), \
                    patch.object(startup.subprocess, 'run', side_effect=timeout), \
                    patch.object(startup, 'prepare') as prepare, \
                    patch.object(startup.sys, 'argv', ['vector_startup.py']), \
                    patch.dict(startup.os.environ, {'QDRANT_API_KEY': 'never-forward'}):
                with self.assertRaises((subprocess.TimeoutExpired, subprocess.CalledProcessError)):
                    startup.run()
                self.assertFalse(transports[-1].exists())
                self.assertTrue((cache / 'work/.dvc').is_dir())
                self.assertEqual(sentinel.read_bytes(), b'keep runtime')
                prepare.assert_not_called()
        self.assertEqual(len(transports), 2)

    def test_cli_failure_secret_safe(self):
        result = subprocess.run([sys.executable, '-I', str(ROOT / 'scripts/vector_startup.py'), '--pull', '/missing'],
                                capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Vector startup failed:', result.stderr)
        self.assertNotIn('Traceback', result.stderr)
        self.assertNotIn('/missing', result.stderr)

    def test_one_time_reset_scope(self):
        text = (ROOT / 'docker-compose.vector-reset.yml').read_text()
        services, volumes = text.split('\nvolumes:\n')
        self.assertEqual(re.findall(r'^  ([\w-]+):$', services, re.M), ['backend', 'qdrant', 'vector-init'])
        self.assertEqual(volumes.splitlines(), ['  qdrant_data:', '  vector_runtime:'])
        self.assertEqual(re.findall(r'^      - (.+)$', services, re.M), [
            'vector_runtime:/vector-runtime', 'qdrant_data:/qdrant/storage', 'vector_runtime:/vector-runtime'])
        self.assertEqual(re.findall(r'^    image: (.+)$', services, re.M), [
            'dev-backend:latest', 'qdrant/qdrant:v1.19.2', 'dev-vector-init:latest'])
        # No reset execution, alternate volume names, mounts or dependencies hidden in this down-only file.
        self.assertEqual(set(re.findall(r'^\s*([\w-]+):', text, re.M)), {
            'services', 'backend', 'qdrant', 'vector-init', 'image', 'volumes', 'qdrant_data', 'vector_runtime'})

    def test_one_time_reset_is_not_normal_startup(self):
        for path in list(ROOT.glob('docker-compose*.yml')) + list((ROOT / '.github/workflows').glob('*.yml')):
            if path.name == 'docker-compose.vector-reset.yml':
                continue
            with self.subTest(path=path.name):
                self.assertNotIn('docker-compose.vector-reset.yml', path.read_text())
        self.assertIn('condition: service_completed_successfully', (ROOT / 'docker-compose.override.yml').read_text())

    def test_one_time_reset_documented_invocation(self):
        text = (ROOT / 'docs/QDRANT_HANDOFF.md').read_text()
        reset = 'docker compose -p dev -f docker-compose.vector-reset.yml down --volumes'
        original = 'docker compose -p dev -f docker-compose.yml -f docker-compose.dev.yml -f docker-compose.override.yml'
        self.assertIn(original + ' ps -a', text)
        commands = [
            original + ' stop backend community-moderation-worker lodging-warmer course-place-warmer qdrant vector-init',
            reset,
            original + ' up -d --no-build --force-recreate --wait --wait-timeout 4500 backend frontend jev-browser lodging-warmer community-moderation-worker',
        ]
        blocks = re.findall(r'```sh\n(.*?)\n\s*```', text, re.S)
        block, = [block for block in blocks if reset in block]
        block = '\n'.join(line.strip() for line in block.splitlines())
        self.assertEqual(block, ' &&\n'.join(commands))
        self.assertNotIn('xargs', block)
        # Execute the actual runbook block with only a shell stub, never real Docker.
        stub = '''calls=0
        docker() {
            calls=$((calls + 1))
            printf '%s\\n' "docker $*"
            if [ "$calls" -eq "$failure" ]; then return 17; fi
        }
        '''
        for failure, count in ((1, 1), (2, 2), (0, 3)):
            with self.subTest(failure=failure):
                result = subprocess.run(['/bin/sh', '-c', 'failure=$1\n' + stub + block, 'runbook', str(failure)],
                                        env={'PATH': '/nonexistent'}, capture_output=True, text=True)
                self.assertEqual(result.returncode, 17 if failure else 0, result.stderr)
                self.assertEqual(result.stdout.splitlines(), commands[:count])
                self.assertEqual(result.stderr, '')
        self.assertIn('logs --no-color --tail 100 vector-init', text)
        for retained in ('vector_cache', 'postgres_data', 'minio_data', 'Drive'):
            self.assertIn(retained, text)

    def test_ci_and_deployment_wiring(self):
        command = 'python -I .github/evaluation_ci.py offline-exec scripts/tests/test_vector_startup.py'
        self.assertIn(command, (ROOT / '.github/workflows/evaluation-regression.yml').read_text())
        overlay = (ROOT / 'docker-compose.override.yml').read_text()
        self.assertIn('condition: service_completed_successfully', overlay)
        self.assertIn('HIGHLIGHT_MULTIMODAL_CORPUS: /vector-runtime/highlight-corpus', overlay)
        for name, default, target in (('VECTOR_DVC_CONFIG', './.dvc/config.local', 'dvc_config'),
                                      ('VECTOR_DRIVE_USER', './.secrets/drive-user.json', 'drive_user')):
            self.assertIn('source: ${' + name + ':-' + default + '}', overlay)
            self.assertIn('target: /run/secrets/' + target, overlay)
            self.assertIn(name + '=' + default, (ROOT / '.env.example').read_text())
        self.assertEqual(overlay.count('read_only: true'), 2)
        self.assertEqual(overlay.count('create_host_path: false'), 2)
        self.assertNotIn('VECTOR_GDRIVE_', overlay)
        self.assertNotIn('VECTOR_GDRIVE_', (ROOT / '.env.example').read_text())
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
        self.assertIn('dvc[gdrive]==3.67.1', dockerfile)
        self.assertIn('pyOpenSSL==24.2.1', dockerfile)
        self.assertIn('import OpenSSL; import pydrive2.fs', dockerfile)
        self.assertNotIn('gdown', dockerfile)
        self.assertNotIn('requirements-data.txt', dockerfile)
        self.assertIn('!data/artifacts/document-vectors.dvc', (ROOT / 'docker/vector-startup.Dockerfile.dockerignore').read_text())


if __name__ == '__main__':
    unittest.main(verbosity=2)
