from contextlib import closing
import importlib.util
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('highlight_corpus', Path(__file__).resolve().parents[1] / 'highlight_corpus.py')
corpus = importlib.util.module_from_spec(spec)
spec.loader.exec_module(corpus)


class CorpusTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.source = self.root / 'source'
        self.source.mkdir()
        (self.source / 'clips').mkdir()
        for suffix in ('.mp4', '.wav'):
            (self.source / 'clips' / ('one' + suffix)).write_bytes(b'media' + suffix.encode())
        (self.source / 'segments.json').write_text(json.dumps({'one': {'text': 'hello'}}))
        (self.source / 'index_mode.json').write_text('"separate"')
        (self.source / 'ingestion_complete.json').write_text(json.dumps({'status': 'complete', 'segments': 1, 'index_mode': 'separate', 'dimensions': 768}))
        (self.source / 'qdrant').mkdir()
        (self.source / 'qdrant' / 'meta.json').write_text('{}')
        for name, table in [('transcripts.sqlite3', 'transcript'), ('qdrant/storage.sqlite', 'points')]:
            with closing(sqlite3.connect(self.source / name)) as db, db:
                db.execute(f'CREATE TABLE {table}(id TEXT, text TEXT)')
                db.execute(f'INSERT INTO {table} VALUES (?, ?)', ('one', 'hello'))
        (self.source / 'web.lock').write_text('lock')
        (self.source / 'qdrant' / '.lock').write_text('lock')
        self.backup = self.root / 'backup'
        self.restored = self.root / 'runtime'

    def make_backup(self):
        return corpus.transfer(self.source, self.backup)

    def test_backup_restore_exact_and_sqlite_integrity(self):
        before = {name: corpus.digest(self.source / name) for name in corpus.inventory(self.source)}
        manifest = self.make_backup()
        corpus.transfer(self.backup, self.restored, restore=True)
        self.assertEqual(corpus.validate(self.restored), manifest)
        self.assertEqual(before, {name: corpus.digest(self.source / name) for name in corpus.inventory(self.source)})
        self.assertFalse((self.restored / 'web.lock').exists())
        self.assertEqual(manifest['files']['transcripts.sqlite3']['sqlite']['tables'], {'transcript': 1})
        for name, item in manifest['files'].items():
            self.assertEqual(corpus.digest(self.restored / name), item['sha256'])
            if 'sqlite' not in item:
                self.assertEqual((self.restored / name).read_bytes(), (self.source / name).read_bytes())

    def test_incidental_metadata_changes_and_legacy_checks(self):
        manifest = self.make_backup()
        digest = corpus.digest
        def metadata_during_copy(path):
            stage = next((parent for parent in path.parents if parent.name == 'corpus'
                          and parent.parent.name.startswith('.highlight-stage-')), None)
            if stage is not None:
                (stage / '.DS_Store').write_text(str(path))
                (stage / 'clips/.DS_Store').write_text(str(path))
            return digest(path)
        with patch.object(corpus, 'digest', side_effect=metadata_during_copy):
            corpus.transfer(self.backup, self.restored, restore=True)
        self.assertEqual(corpus.validate(self.restored), manifest)
        readiness = corpus.readiness
        def change_during_validation(root, name):
            result = readiness(root)
            (root / name).write_text('changed during validation')
            return result
        for name, accepted in (('.DS_Store', True), ('.unexpected', False), ('clips/one.mp4', False)):
            with self.subTest(name=name):
                original = (self.restored / name).read_bytes() if (self.restored / name).exists() else None
                with patch.object(corpus, 'readiness', side_effect=lambda root: change_during_validation(root, name)):
                    if accepted:
                        corpus.validate(self.restored)
                    else:
                        with self.assertRaises(ValueError):
                            corpus.validate(self.restored)
                if original is None:
                    (self.restored / name).unlink()
                else:
                    (self.restored / name).write_bytes(original)
        metadata = self.restored / '.DS_Store'
        metadata.unlink()
        metadata.symlink_to(self.source / 'segments.json')
        with self.assertRaises(ValueError):
            corpus.validate(self.restored)
        metadata.unlink()
        # Older trusted manifests may explicitly include metadata: copy/check it, not ignore it.
        metadata = self.backup / 'clips/.DS_Store'
        metadata.write_text('legacy tracked metadata')
        manifest['files']['clips/.DS_Store'] = {'size': metadata.stat().st_size, 'sha256': digest(metadata)}
        (self.backup / corpus.MANIFEST).write_text(json.dumps(manifest))
        corpus.validate(self.backup)
        legacy = self.root / 'legacy-runtime'
        corpus.transfer(self.backup, legacy, restore=True)
        self.assertEqual((legacy / 'clips/.DS_Store').read_bytes(), metadata.read_bytes())
        metadata.write_text('tampered legacy metadata')
        with self.assertRaises(ValueError):
            corpus.validate(self.backup)

    def test_live_sqlite_online_backup(self):
        with closing(sqlite3.connect(self.source / 'transcripts.sqlite3')) as db, db:
            db.execute('PRAGMA journal_mode=WAL')
            db.execute('INSERT INTO transcript VALUES (?, ?)', ('two', 'wal'))
            db.commit()
            manifest = self.make_backup()
        self.assertEqual(manifest['files']['transcripts.sqlite3']['sqlite']['tables']['transcript'], 2)
        self.assertFalse((self.backup / 'transcripts.sqlite3-wal').exists())

    def test_existing_destinations_refused(self):
        self.make_backup()
        for destination in (self.backup, self.source):
            with self.assertRaises((FileExistsError, ValueError)):
                corpus.transfer(self.source, destination)
        self.restored.mkdir()
        (self.restored / 'unrelated').write_text('keep')
        with self.assertRaises(FileExistsError):
            corpus.transfer(self.backup, self.restored, restore=True)
        self.assertEqual((self.restored / 'unrelated').read_text(), 'keep')

    def test_exclusive_publication_race(self):
        stage = self.root / 'stage'
        stage.mkdir()
        self.restored.mkdir()
        with self.assertRaises(OSError):
            corpus.publish(stage, self.restored)
        self.assertTrue(stage.exists())

    def test_manifest_tampering_and_malformed(self):
        self.make_backup()
        path = self.backup / corpus.MANIFEST
        original = path.read_text()
        for replacement in ('{', '[]', '{"version":1,"files":[]}', original.replace('"size": 10', '"size": -1')):
            if replacement == original:
                continue
            path.write_text(replacement)
            with self.assertRaises((ValueError, TypeError)):
                corpus.transfer(self.backup, self.restored, restore=True)
            self.assertFalse(self.restored.exists())
        path.write_text(original)
        (self.backup / 'clips/one.mp4').write_bytes(b'tampered')
        with self.assertRaises(ValueError):
            corpus.transfer(self.backup, self.restored, restore=True)
        self.assertFalse(self.restored.exists())

    def test_manifest_path_escapes(self):
        manifest = self.make_backup()
        for name in ('../outside', '/absolute', 'clips/../../outside', 'clips//one.mp4', 'clips\\one.mp4'):
            with self.subTest(name=name):
                altered = dict(manifest, files={name: {'size': 0, 'sha256': 'a' * 64}})
                (self.backup / corpus.MANIFEST).write_text(json.dumps(altered))
                with self.assertRaises(ValueError):
                    corpus.transfer(self.backup, self.restored, restore=True)
                self.assertFalse(self.restored.exists())

    def test_symlinks_rejected(self):
        (self.source / 'escape').symlink_to(self.root)
        with self.assertRaises(ValueError):
            self.make_backup()
        (self.source / 'escape').unlink()
        self.restored.symlink_to(self.source, target_is_directory=True)
        with self.assertRaises(ValueError):
            corpus.transfer(self.source, self.restored)
        self.restored.unlink()
        alias = self.root / 'alias'
        alias.symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(ValueError):
            corpus.transfer(self.source, alias / 'nested')
        self.make_backup()
        (self.backup / 'clips/one.wav').unlink()
        (self.backup / 'clips/one.wav').symlink_to(self.source / 'clips/one.wav')
        with self.assertRaises(ValueError):
            corpus.transfer(self.backup, self.restored, restore=True)

    def test_incomplete_and_missing_media(self):
        marker = self.source / 'ingestion_complete.json'
        original = marker.read_text()
        marker.write_text('{"status":"pending"}')
        with self.assertRaises(ValueError):
            self.make_backup()
        marker.write_text(original)
        pending = self.source / 'ingestion_pending.json'
        pending.write_text('{"plan_sha256":"new-unfinished-plan"}')
        with self.assertRaises(ValueError):
            self.make_backup()
        pending.unlink()
        (self.source / 'clips/one.wav').unlink()
        with self.assertRaises(ValueError):
            self.make_backup()
        self.assertFalse(self.backup.exists())

    def test_source_mutation_and_failed_staging_cleanup(self):
        copy = corpus.shutil.copyfile
        def mutating(src, dst):
            result = copy(src, dst)
            (self.source / 'new').write_text('mutation')
            return result
        with patch.object(corpus.shutil, 'copyfile', side_effect=mutating):
            with self.assertRaises(ValueError):
                self.make_backup()
        self.assertFalse(self.backup.exists())
        self.assertEqual(list(self.root.glob('.highlight-stage-*')), [])

    def test_overlapping_paths_and_disk_refused(self):
        with self.assertRaises(ValueError):
            corpus.transfer(self.source, self.source / 'nested')
        with patch.object(corpus.shutil, 'disk_usage', return_value=corpus.shutil._ntuple_diskusage(1, 1, 0)):
            with self.assertRaises(ValueError):
                self.make_backup()
        self.assertFalse(self.backup.exists())


if __name__ == '__main__':
    unittest.main(verbosity=2)
