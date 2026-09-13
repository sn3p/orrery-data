"""Object serialization order cannot change schema-defined identity bytes."""

from pathlib import Path
import sqlite3
import unittest

import test_releases
from orrery_data.pipeline import refresh
from orrery_data.storage import URLS, digest, encode, file_info, read_json, write_json


def reverse_objects(value):
    if isinstance(value, dict):
        return {key: reverse_objects(item) for key, item in reversed(list(value.items()))}
    if isinstance(value, list):
        return [reverse_objects(item) for item in value]
    return value


class IdentityOrder(unittest.TestCase):
    setUpClass = classmethod(test_releases.ReleaseCLI.setUpClass.__func__)
    tearDownClass = classmethod(test_releases.ReleaseCLI.tearDownClass.__func__)
    setUp = test_releases.ReleaseCLI.setUp
    cli = test_releases.ReleaseCLI.cli
    prepare = test_releases.ReleaseCLI.prepare
    http_args = test_releases.ReleaseCLI.http_args
    reseal = test_releases.ReleaseCLI.reseal
    tree = test_releases.ReleaseCLI.tree

    def test_release_reordering_preserves_id_and_noncanonical_hash_cannot_activate(self):
        prepared = self.prepare()
        bundle = Path(prepared['path'])
        manifest = read_json(bundle / 'release.json')
        manifest['identity'] = reverse_objects(manifest['identity'])
        write_json(bundle / 'release.json', manifest)
        self.reseal(bundle)
        (self.output / 'latest.json').unlink()
        before = self.tree(bundle)
        verified = self.cli('verify-release', '--bundle', bundle)
        self.assertEqual(self.tree(bundle), before)
        self.assertEqual(verified['release_version'], prepared['release_version'])
        reused = self.prepare(offline=prepared['snapshot_version'])
        self.assertEqual(reused['release_version'], prepared['release_version'])
        self.assertEqual(Path(reused['path']), self.output / reused['release_version'])
        self.assertEqual(read_json(self.output / 'latest.json')['release_version'], reused['release_version'])
        manifest['release_version'] = 'release-v1-' + digest(manifest['identity'])
        self.assertNotEqual(manifest['release_version'], prepared['release_version'])
        write_json(bundle / 'release.json', manifest)
        self.reseal(bundle)
        (self.output / 'latest.json').unlink()
        before = self.tree(self.output)
        self.assertIn('identity mismatch', self.cli('verify-release', '--bundle', bundle, code=1)['error'])
        self.assertIn('identity mismatch', self.prepare(offline=prepared['snapshot_version'], code=1)['error'])
        self.assertEqual(self.tree(self.output), before)

    def test_snapshot_order_is_stable_for_readers_and_reversed_api_source_mapping(self):
        local = {name: test_releases.ROOT / 'tests/fixtures' / filename
                 for name, filename in [('mpcorb', 'MPCORB.DAT'), ('numbered', 'NumberedMPs.txt')]}
        snapshot = refresh(self.store, URLS, local, {}, 60)
        reversed_source = refresh(self.directory / 'other-store', dict(reversed(list(URLS.items()))), local, {}, 60)
        self.assertEqual(reversed_source['snapshot_version'], snapshot['snapshot_version'])
        path = self.store / 'snapshots' / snapshot['snapshot_version'] / 'snapshot.json'
        manifest = read_json(path)
        before_export = self.cli('export', '--store', self.store, '--output', self.output)
        database = self.directory / 'database.sqlite3'
        before_db = self.cli('build-db', '--store', self.store, '--database', database)
        write_json(path, reverse_objects(manifest))
        self.assertEqual(self.cli('export', '--store', self.store, '--output', self.output), before_export)
        after_db = self.cli('build-db', '--store', self.store, '--database', database)
        self.assertEqual(after_db['database_version'], before_db['database_version'])
        self.cli('db-info', '--database', database, '--verify')
        malformed = reverse_objects(manifest)
        malformed['snapshot_version'] = 'snapshot-v1-' + digest(malformed['identity'])
        original = path.parent
        moved = original.with_name(malformed['snapshot_version'])
        self.assertNotEqual(moved, original)
        original.rename(moved)
        write_json(moved / 'snapshot.json', malformed)
        before = self.tree(self.store)
        self.cli('export', '--store', self.store, '--snapshot', malformed['snapshot_version'],
                 '--output', self.output, code=1)
        self.assertEqual(self.tree(self.store), before)

    def test_export_order_cannot_change_cached_identity(self):
        prepared = self.prepare()
        output = self.directory / 'standalone-exports'
        exported = self.cli('export', '--store', self.store, '--output', output)
        target = Path(exported['path'])
        manifest = read_json(target / 'manifest.json')

        def reseal():
            write_json(target / 'manifest.json', manifest)
            (target / 'SHA256SUMS').write_text(''.join(
                f"{file_info(target / name)['sha256']}  {name}\n"
                for name in sorted([*manifest['artifacts'], 'manifest.json'])))

        manifest = reverse_objects(manifest)
        reseal()
        self.assertEqual(self.cli('export', '--store', self.store, '--output', output), exported)
        manifest['data_version'] = 'export-v1-' + digest(manifest['identity'])
        self.assertNotEqual(manifest['data_version'], exported['data_version'])
        reseal()
        before = self.tree(output)
        self.assertIn('identity', self.cli('export', '--store', self.store, '--output', output, code=1)['error'])
        self.assertEqual(self.tree(output), before)

    def test_database_order_cannot_change_standalone_identity(self):
        prepared = self.prepare()
        database = self.directory / 'database.sqlite3'
        self.cli('build-db', '--store', self.store, '--database', database)
        original = self.cli('db-info', '--database', database)
        reordered = reverse_objects(original['identity'])
        with sqlite3.connect(database) as connection:
            connection.execute("UPDATE metadata SET value=? WHERE key='identity'", (encode(reordered),))
        self.assertEqual(self.cli('db-info', '--database', database)['database_version'], original['database_version'])
        self.cli('query', '--database', database)
        with sqlite3.connect(database) as connection:
            connection.execute("UPDATE metadata SET value=? WHERE key='database_version'",
                               (encode('sqlite-v1-' + digest(reordered)),))
        before = self.tree(self.directory)
        for command in ('db-info', 'query'):
            self.assertIn('identity mismatch', self.cli(command, '--database', database, code=1)['error'])
            self.assertEqual(self.tree(self.directory), before)

    def test_previous_release_pointer_must_identify_the_contained_release(self):
        prepared = self.prepare()
        bundle = Path(prepared['path'])
        wrong = 'release-v1-' + 'a' * 64
        bundle.rename(self.output / wrong)
        write_json(self.output / 'latest.json', {'release_version': wrong, 'manifest': prepared['manifest']})
        before = self.tree(self.output)
        self.assertIn('identity differs from its pointer', self.prepare(offline=prepared['snapshot_version'], code=1)['error'])
        self.assertEqual(self.tree(self.output), before)
