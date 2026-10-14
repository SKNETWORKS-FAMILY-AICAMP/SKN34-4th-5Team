"""Offline, staged copies of a completed local highlight corpus; never open Qdrant."""
import argparse
from contextlib import closing
import ctypes
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import stat
import sys
import tempfile

MANIFEST = 'backup_manifest.json'


def safe_root(value):
    path = Path(os.path.abspath(value))
    for part in (path, *path.parents):
        if part.is_symlink():
            raise ValueError(f'Symlink path: {part}')
    return path


def inventory(root):
    result = {}
    if not root.is_dir():
        raise ValueError('Missing corpus directory')
    for path in sorted(root.rglob('*')):
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode) or not (stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode)):
            raise ValueError(f'Unsafe corpus path: {path}')
        if path.is_file():
            result[path.relative_to(root).as_posix()] = (info.st_size, info.st_mtime_ns, info.st_ctime_ns, info.st_ino)
    return result


def source_inventory(root):
    # SQLite readers update shared-memory lock bytes; main DB and WAL still detect writes.
    return {name: info for name, info in inventory(root).items() if not name.endswith('-shm')}


def digest(path):
    with path.open('rb') as file:
        return hashlib.file_digest(file, 'sha256').hexdigest()


def transient(name):
    return name in ('web.lock', 'qdrant/.lock') or name.endswith(('-wal', '-shm', '-journal'))


def readonly(path):
    return sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)


def sqlite_info(path):
    with closing(readonly(path)) as db:
        if db.execute('PRAGMA integrity_check').fetchall() != [('ok',)]:
            raise ValueError(f'SQLite integrity failed: {path}')
        tables = [row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
        counts = {name: db.execute('SELECT count(*) FROM "' + name.replace('"', '""') + '"').fetchone()[0] for name in tables}
        logical = hashlib.sha256()
        for line in db.iterdump():
            logical.update(line.encode('utf-8') + b'\n')
        return {'tables': counts, 'logical_sha256': logical.hexdigest()}


def readiness(root):
    rows = json.loads((root / 'segments.json').read_text())
    marker = json.loads((root / 'ingestion_complete.json').read_text())
    if (not isinstance(rows, dict) or not rows or not isinstance(marker, dict) or marker.get('status') != 'complete'
            or marker.get('segments') != len(rows) or marker.get('index_mode') != 'separate'
            or json.loads((root / 'index_mode.json').read_text()) != 'separate'
            or not (root / 'qdrant').is_dir() or not (root / 'transcripts.sqlite3').is_file()):
        raise ValueError('Incomplete or inconsistent corpus')
    pending = root / 'ingestion_pending.json'
    if pending.exists():
        plan = json.loads(pending.read_text())
        if (not isinstance(plan, dict) or not plan.get('plan_sha256')
                or plan['plan_sha256'] != marker.get('plan_sha256')):
            raise ValueError('Unfinished ingestion plan')
    for key in rows:
        if not isinstance(key, str) or '/' in key or '\\' in key or key in ('.', '..'):
            raise ValueError('Unsafe segment ID')
        for suffix in ('.mp4', '.wav'):
            if not (root / 'clips' / (key + suffix)).is_file():
                raise ValueError('Missing segment media')
    return {'segments': len(rows), 'index_mode': marker['index_mode'], 'dimensions': marker.get('dimensions')}


def publish(stage, destination):
    # Native exclusive rename also refuses a destination created after preflight.
    libc = ctypes.CDLL(None, use_errno=True)
    if sys.platform == 'darwin':
        status = libc.renamex_np(os.fsencode(stage), os.fsencode(destination), 4)
    elif sys.platform.startswith('linux'):
        status = libc.renameat2(-100, os.fsencode(stage), -100, os.fsencode(destination), 1)
    else:
        raise ValueError('Exclusive publication supports macOS and Linux only')
    if status:
        code = ctypes.get_errno()
        raise OSError(code, os.strerror(code), str(destination))


def validate(root):
    before = inventory(root)
    manifest = json.loads((root / MANIFEST).read_text())
    if not isinstance(manifest, dict) or manifest.get('version') != 1 or not isinstance(manifest.get('files'), dict):
        raise ValueError('Invalid backup manifest')
    files = manifest['files']
    for name, item in files.items():
        path = Path(name)
        if (not name or path.is_absolute() or path.as_posix() != name or '\\' in name
                or '..' in path.parts or name == MANIFEST or transient(name)
                or not isinstance(item, dict) or type(item.get('size')) is not int or item['size'] < 0
                or not isinstance(item.get('sha256'), str) or len(item['sha256']) != 64):
            raise ValueError('Invalid manifest file entry')
    if set(before) != set(files) | {MANIFEST}:
        raise ValueError('Backup file inventory mismatch')
    for name, item in files.items():
        path = root / name
        if path.stat().st_size != item['size'] or digest(path) != item['sha256']:
            raise ValueError(f'Backup checksum mismatch: {name}')
        if 'sqlite' in item and sqlite_info(path) != item['sqlite']:
            raise ValueError(f'SQLite logical mismatch: {name}')
    if readiness(root) != manifest.get('corpus'):
        raise ValueError('Backup corpus metadata mismatch')
    if inventory(root) != before:
        raise ValueError('Backup changed during validation')
    return manifest


def transfer(source, destination, restore=False):
    source, destination = safe_root(source), safe_root(destination)
    if destination.exists() or destination.is_symlink():
        raise FileExistsError(f'Destination already exists: {destination}')
    if source == destination or source in destination.parents or destination in source.parents:
        raise ValueError('Source and destination must not overlap')
    before = source_inventory(source)
    if MANIFEST in before and not restore:
        raise ValueError('Source already has a backup manifest')
    manifest = validate(source) if restore else None
    corpus = readiness(source)
    hashes = {name: digest(source / name) for name in before}
    if source_inventory(source) != before:
        raise ValueError('Source changed during preflight')
    destination.parent.mkdir(parents=True, exist_ok=True)
    size = sum(item[0] for item in before.values())
    if shutil.disk_usage(destination.parent).free < size + 64 * 1024 * 1024:
        raise ValueError('Insufficient free disk for staged copy')
    with tempfile.TemporaryDirectory(prefix='.highlight-stage-', dir=destination.parent) as temp:
        stage = Path(temp) / 'corpus'
        stage.mkdir()
        files = {}
        for name in before:
            if transient(name) or name == MANIFEST:
                continue
            src, dst = source / name, stage / name
            dst.parent.mkdir(parents=True, exist_ok=True)
            with src.open('rb') as file:
                is_sqlite = file.read(16) == b'SQLite format 3\x00'
            sql = None
            if is_sqlite and not restore:
                sql = sqlite_info(src)
                with closing(readonly(src)) as db, closing(sqlite3.connect(dst)) as target:
                    db.backup(target)
                    target.execute('PRAGMA journal_mode=DELETE')
                if sqlite_info(dst) != sql:
                    raise ValueError(f'SQLite changed during backup: {name}')
            else:
                shutil.copyfile(src, dst)
                if digest(dst) != hashes[name]:
                    raise ValueError(f'Copy checksum mismatch: {name}')
            files[name] = {'size': dst.stat().st_size, 'sha256': digest(dst)}
            if is_sqlite:
                files[name]['sqlite'] = sql or sqlite_info(dst)
        if source_inventory(source) != before or any(digest(source / name) != hashes[name] for name in before):
            raise ValueError('Source changed during copy')
        if source_inventory(source) != before:
            raise ValueError('Source changed during final checksum')
        if restore:
            shutil.copyfile(source / MANIFEST, stage / MANIFEST)
        else:
            manifest = {'version': 1, 'corpus': corpus, 'files': files,
                        'source_sha256': hashes, 'omitted': [name for name in before if transient(name)]}
            (stage / MANIFEST).write_text(json.dumps(manifest, indent=2, sort_keys=True) + '\n')
        validate(stage)
        if source_inventory(source) != before:
            raise ValueError('Source changed before publication')
        publish(stage, destination)
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('backup', 'restore', 'verify'))
    parser.add_argument('source', type=Path)
    parser.add_argument('destination', type=Path, nargs='?')
    args = parser.parse_args()
    try:
        if args.command == 'verify':
            if args.destination:
                parser.error('verify takes only a backup path')
            result = validate(safe_root(args.source))
        else:
            if args.destination is None:
                parser.error('backup/restore requires a NEW destination')
            result = transfer(args.source, args.destination, args.command == 'restore')
        print(json.dumps({'corpus': result['corpus'], 'files': len(result['files']),
                          'bytes': sum(item['size'] for item in result['files'].values())}))
    except (OSError, ValueError, sqlite3.Error, KeyError, TypeError) as error:
        parser.exit(1, f'Corpus operation failed: {error}\n')


if __name__ == '__main__':
    main()
