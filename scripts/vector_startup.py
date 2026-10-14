"""One-shot, no-destroy DVC vector preparation. No app/provider dependencies."""
import configparser
from contextlib import closing
import fcntl
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request

spec = importlib.util.spec_from_file_location('highlight_corpus', Path(__file__).with_name('highlight_corpus.py'))
corpus = importlib.util.module_from_spec(spec)
spec.loader.exec_module(corpus)
ARTIFACTS = ('document-vectors', 'highlight-corpus')
ORIGIN = 'vector_startup_snapshot_sha256'


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


class Qdrant:
    def __init__(self, url, key):
        parsed = urllib.parse.urlsplit(url)
        if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.query or parsed.fragment:
            raise ValueError('Invalid Qdrant endpoint')
        self.url, self.key = url.rstrip('/'), key
        self.opener = urllib.request.build_opener(NoRedirect())

    def request(self, method, path, data=None, content_type='application/json', missing=False):
        if isinstance(data, dict):
            data = json.dumps(data).encode()
        request = urllib.request.Request(self.url + path, data=data, method=method,
                                         headers={'api-key': self.key, 'Content-Type': content_type})
        try:
            with self.opener.open(request, timeout=300) as response:
                return json.load(response)
        except urllib.error.HTTPError as error:
            if missing and error.code == 404:
                return None
            raise ValueError('Qdrant request failed') from None

    def wait(self):
        for attempt in range(30):
            try:
                return self.request('GET', '/')
            except urllib.error.URLError:
                if attempt == 29:
                    raise ValueError('Qdrant unavailable') from None
                time.sleep(2)

    def upload(self, path, snapshot):
        # ponytail: bounded 46MB snapshot in memory; stream multipart if artifacts grow.
        boundary = 'vector-startup-snapshot'
        data = (f'--{boundary}\r\nContent-Disposition: form-data; name="snapshot"; filename="documents.snapshot"\r\n'
                'Content-Type: application/octet-stream\r\n\r\n').encode() + snapshot.read_bytes() + f'\r\n--{boundary}--\r\n'.encode()
        self.request('POST', path + '/snapshots/upload?wait=true&priority=snapshot', data,
                     'multipart/form-data; boundary=' + boundary)


def document_manifest(root, model):
    corpus.inventory(root)
    manifest = json.loads((root / 'manifest.json').read_text())
    name = manifest.get('snapshot_file')
    if (manifest.get('artifact_type') != 'qdrant-native-collection-snapshot'
            or manifest.get('embedding_model') != model or model != 'text-embedding-3-small'
            or manifest.get('dimension') != 1536 or manifest.get('distance') != 'Cosine'
            or manifest.get('qdrant_server_version') != '1.19.2'
            or type(manifest.get('expected_points')) is not int or manifest['expected_points'] <= 0
            or not isinstance(name, str) or Path(name).name != name or name in ('', '.', '..')
            or not re.fullmatch('[0-9a-f]{64}', manifest.get('snapshot_sha256', ''))):
        raise ValueError('Incompatible document manifest')
    snapshot = root / name
    if snapshot.stat().st_size != manifest.get('snapshot_bytes') or corpus.digest(snapshot) != manifest['snapshot_sha256']:
        raise ValueError('Document snapshot checksum mismatch')
    return manifest, snapshot


def check_documents(info, manifest, owned=True):
    config = info['config']
    vectors = config['params']['vectors']
    metadata = config.get('metadata') or {}
    if (vectors.get('size') != manifest['dimension'] or vectors.get('distance') != manifest['distance']
            or metadata.get('embedding_model') != manifest['embedding_model']
            or info.get('points_count', 0) <= 0
            or (owned and metadata.get(ORIGIN) != manifest['snapshot_sha256'])):
        raise ValueError('Occupied or incompatible document collection; operator resolution required')


def prepare_documents(db, name, manifest, snapshot):
    if not re.fullmatch('[a-zA-Z0-9_-]+', name):
        raise ValueError('Invalid collection name')
    path = '/collections/' + name
    existing = db.request('GET', path, missing=True)
    if existing is not None:
        check_documents(existing['result'], manifest)
        return 'reused'
    # Exclusive reservation; snapshot recovery keeps target metadata, so bind the model here.
    db.request('PUT', path, {'vectors': {'size': manifest['dimension'], 'distance': manifest['distance']},
                             'metadata': {'vector_startup_pending': manifest['snapshot_sha256'],
                                          'embedding_model': manifest['embedding_model'], 'dimension': manifest['dimension']}})
    reserved = db.request('GET', path)['result']
    if reserved.get('points_count') != 0 or (reserved['config'].get('metadata') or {}).get('vector_startup_pending') != manifest['snapshot_sha256']:
        raise ValueError('Collection reservation lost')
    db.upload(path, snapshot)
    restored = db.request('GET', path)['result']
    check_documents(restored, manifest, owned=False)
    if restored['points_count'] != manifest['expected_points']:
        raise ValueError('Restored document count mismatch')
    metadata = dict(restored['config'].get('metadata') or {}, **{ORIGIN: manifest['snapshot_sha256']})
    metadata.pop('vector_startup_pending', None)
    db.request('PATCH', path, {'metadata': metadata})
    check_documents(db.request('GET', path)['result'], manifest)
    return 'restored'


def check_highlight(root):
    corpus.inventory(root)  # Path types only: writable data is not an immutable backup.
    ready = corpus.readiness(root)
    if json.loads((root / 'ingestion_complete.json').read_text()).get('model') != 'gemini-embedding-2':
        raise ValueError('Incompatible highlight embedding model')
    meta = json.loads((root / 'qdrant/meta.json').read_text())
    vectors = meta['collections']['highlight_multimodal_experiment_v1']['vectors']
    if ready['dimensions'] != 768 or set(vectors) != {'video', 'audio', 'text'} or any(
            vector.get('size') != 768 or vector.get('distance') != 'Cosine' for vector in vectors.values()):
        raise ValueError('Incompatible highlight runtime')
    for path, table in ((root / 'qdrant/collection/highlight_multimodal_experiment_v1/storage.sqlite', 'points'),
                        (root / 'transcripts.sqlite3', 'transcript')):
        with closing(corpus.readonly(path)) as db:
            if db.execute('SELECT count(*) FROM ' + table).fetchone()[0] < ready['segments']:
                raise ValueError('Incomplete highlight stored data')
    return ready


def prepare(artifacts, runtime, db, name, model):
    artifacts, runtime = corpus.safe_root(artifacts), corpus.safe_root(runtime)
    if artifacts == runtime or artifacts in runtime.parents or runtime in artifacts.parents:
        raise ValueError('Artifact and runtime paths overlap')
    corpus.inventory(runtime)
    document, snapshot = document_manifest(artifacts / 'document-vectors', model)
    highlight = artifacts / 'highlight-corpus'
    corpus.validate(highlight)
    check_highlight(highlight)
    origin = corpus.digest(highlight / corpus.MANIFEST)
    destination, receipt = runtime / 'highlight-corpus', runtime / 'highlight-origin.json'
    # Check foreign destinations before any remote mutation.
    if destination.exists():
        if json.loads(receipt.read_text()) != {'manifest_sha256': origin} or corpus.digest(destination / corpus.MANIFEST) != origin:
            raise ValueError('Occupied highlight runtime; operator resolution required')
        check_highlight(destination)
    elif receipt.exists():
        raise ValueError('Incomplete highlight runtime')
    version = db.wait().get('version')
    if version != document['qdrant_server_version']:
        raise ValueError('Incompatible Qdrant server version')
    documents = prepare_documents(db, name, document, snapshot)
    if not destination.exists():
        corpus.transfer(highlight, destination, restore=True)
        check_highlight(destination)
        with receipt.open('x') as file:
            json.dump({'manifest_sha256': origin}, file)
        highlights = 'restored'
    else:
        highlights = 'reused'
    return {'documents': documents, 'highlights': highlights}


def pull(work):
    """Child process, bounded by parent. Keep selected remote/auth only in memory."""
    from dvc.repo import Repo
    from pydrive2.auth import GoogleAuth
    import httplib2

    config = configparser.ConfigParser(interpolation=None)
    config.read('/run/secrets/dvc_config')
    selected = 'remote "highlight-drive"'
    section = next(section for section in config.sections() if section.strip("'\"") == selected)
    remote = dict(config[section])
    if not re.fullmatch(r'gdrive://[a-zA-Z0-9_-]+(?:/[a-zA-Z0-9_-]+)*', remote.get('url', '')):
        raise ValueError('Only selected private Drive remote is supported')
    if set(remote) - {'url', 'gdrive_client_id', 'gdrive_client_secret', 'gdrive_user_credentials_file'}:
        raise ValueError('Unsupported Drive configuration')
    credentials = json.loads(Path('/run/secrets/drive_user').read_text())
    if not credentials.get('refresh_token') or credentials.get('token_uri') not in (
            'https://oauth2.googleapis.com/token', 'https://accounts.google.com/o/oauth2/token'):
        raise ValueError('Provisioned Google refresh credentials required')

    def noninteractive(auth, *args, **kwargs):
        auth.LoadCredentials()
        if auth.credentials is None:
            raise ValueError('Missing stored Drive credentials')
        if auth.access_token_expired:
            auth.Refresh()
        auth.Authorize()

    GoogleAuth.LocalWebserverAuth = noninteractive
    GoogleAuth.CommandLineAuth = noninteractive
    original_request = httplib2.Http.request

    def google_request(http, uri, *args, **kwargs):
        parsed = urllib.parse.urlsplit(uri)
        if parsed.scheme != 'https' or parsed.hostname not in ('oauth2.googleapis.com', 'accounts.google.com', 'www.googleapis.com'):
            raise ValueError('Untrusted Drive request destination')
        http.follow_redirects = False
        kwargs['redirections'] = 0
        return original_request(http, uri, *args, **kwargs)

    httplib2.Http.request = google_request
    with tempfile.TemporaryDirectory(prefix='vector-auth-') as temp:
        token = Path(temp) / 'drive-user.json'
        token.write_text(json.dumps(credentials))
        token.chmod(0o600)
        remote['gdrive_user_credentials_file'] = str(token)
        with Repo(str(work), config={'core': {'no_scm': True, 'analytics': False}, 'cache': {'type': 'copy'},
                                      'remote': {'highlight-drive': remote}}) as repo:
            repo.pull(targets=['data/artifacts/' + name + '.dvc' for name in ARTIFACTS], remote='highlight-drive')


def run():
    if len(sys.argv) == 3 and sys.argv[1] == '--pull':
        pull(Path(sys.argv[2]))
        return
    cache, runtime = corpus.safe_root('/vector-cache'), corpus.safe_root('/vector-runtime')
    runtime.mkdir(parents=True, exist_ok=True)
    # ponytail: one volume-wide nonblocking lock; split locks only for independent workloads.
    fd = os.open(runtime / '.startup.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        cache.mkdir(parents=True, exist_ok=True)
        corpus.inventory(cache)
        cache_fd = os.open(cache / '.startup.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        with os.fdopen(cache_fd, 'w') as cache_lock:
            fcntl.flock(cache_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            owner = cache / 'startup-owner.json'
            if owner.exists():
                if json.loads(owner.read_text()) != {'version': 1}:
                    raise ValueError('Foreign vector cache')
            else:
                if set(path.name for path in cache.iterdir()) != {'.startup.lock'}:
                    raise ValueError('Occupied vector cache')
                with owner.open('x') as file:
                    json.dump({'version': 1}, file)
            work = cache / 'work'
            work.mkdir(exist_ok=True)
            if not (work / '.dvc').exists():
                from dvc.repo import Repo
                Repo.init(str(work), no_scm=True).close()
            pointers = work / 'data/artifacts'
            pointers.mkdir(parents=True, exist_ok=True)
            for name in ARTIFACTS:
                shutil.copyfile(Path('/opt/vector-startup/pointers') / (name + '.dvc'), pointers / (name + '.dvc'))
            subprocess.run([sys.executable, '-I', __file__, '--pull', str(work)], check=True,
                           stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           timeout=3600, env={'PATH': os.environ['PATH'], 'HOME': '/tmp', 'DVC_NO_ANALYTICS': '1'})
            print(json.dumps(prepare(pointers, runtime, Qdrant(os.environ['QDRANT_URL'], os.environ['QDRANT_API_KEY']),
                                     os.environ['QDRANT_DOCUMENT_COLLECTION'], os.environ['EMBEDDING_MODEL'])))


if __name__ == '__main__':
    try:
        run()
    except Exception:
        # Third-party auth/HTTP exceptions can contain secrets: never print their bodies.
        print('Vector startup failed: pull/auth/integrity/target readiness; inspect private operator configuration', file=sys.stderr)
        sys.exit(1)
