"""Systematic contract mutations plus independently resealed semantic failures."""

import copy
import gzip
import json
from pathlib import Path
import shutil
import sqlite3
import unittest

import test_releases
from orrery_data.contracts import validate_contract
from orrery_data.database import database_info, query_database
from orrery_data.formats import DataError
from orrery_data.pipeline import export, load_snapshot, validate_export_manifest
from orrery_data.releases import prepare_release, verify_release
from orrery_data.storage import digest, file_info, loads_json, read_json, write_json


def mutations(value, path=()):
    """Enumerate malformed structures independently of the schema implementation."""
    if isinstance(value, dict):
        yield path, 'replace', []
        yield path + ('unexpected_schema_extension',), 'replace', 1
        for key, child in value.items():
            if key != 'resolved_url':  # The sole optional stored-source field.
                yield path + (key,), 'delete', None
            yield from mutations(child, path + (key,))
    elif isinstance(value, list):
        yield path, 'replace', {}
        for index, child in enumerate(value):
            yield from mutations(child, path + (index,))
    else:
        yield path, 'replace', {}  # Invalid for every scalar, including nullable ones.
        if type(value) is int:
            yield path, 'replace', bool(value)
            yield path, 'replace', float(value)


def changed(original, path, operation, value):
    result = copy.deepcopy(original)
    if not path:
        return value
    target = result
    for part in path[:-1]:
        target = target[part]
    if operation == 'delete':
        del target[path[-1]]
    else:
        target[path[-1]] = value
    return result


class ReleaseContract(unittest.TestCase):
    setUpClass = classmethod(test_releases.ReleaseCLI.setUpClass.__func__)
    tearDownClass = classmethod(test_releases.ReleaseCLI.tearDownClass.__func__)
    setUp = test_releases.ReleaseCLI.setUp
    cli = test_releases.ReleaseCLI.cli
    prepare = test_releases.ReleaseCLI.prepare
    verify = test_releases.ReleaseCLI.verify
    tree = test_releases.ReleaseCLI.tree
    http_args = test_releases.ReleaseCLI.http_args
    reseal = test_releases.ReleaseCLI.reseal

    def fixture(self):
        result = self.prepare('--selected-limit', 1)
        return result, Path(result['path'])

    def db_metadata(self, bundle):
        with sqlite3.connect(bundle / 'orrery.sqlite3') as db:
            return {k: json.loads(v) for k, v in db.execute('SELECT key,value FROM metadata')}

    def write_db_metadata(self, bundle, value):
        with sqlite3.connect(bundle / 'orrery.sqlite3') as db:
            db.execute('DELETE FROM metadata')
            db.executemany('INSERT INTO metadata VALUES (?,?)', [(k, json.dumps(v)) for k, v in value.items()])

    def reseal_export(self, bundle, profile):
        root = bundle / 'exports' / profile
        manifest = read_json(root / 'manifest.json')
        for name in manifest['artifacts']:
            manifest['artifacts'][name].update(file_info(root / name))
        write_json(root / 'manifest.json', manifest)
        (root / 'SHA256SUMS').write_text(''.join(f"{file_info(root / name)['sha256']}  {name}\n"
                                               for name in sorted([*manifest['artifacts'], 'manifest.json'])))
        self.reseal(bundle)

    def sync_snapshot(self, bundle, snapshot):
        # Reseal every dependent hash and copied field so relational checks
        # cannot hide missing validation of the authoritative snapshot itself.
        write_json(bundle / 'snapshot.json', snapshot)
        release = read_json(bundle / 'release.json')
        release.update(sources=snapshot['sources'], counts=snapshot['counts'])
        database = self.db_metadata(bundle)
        database['snapshot'] = snapshot
        database['identity']['files'] = snapshot['files']
        database['database_version'] = 'sqlite-v1-' + digest(database['identity'])
        release['database_version'] = database['database_version']
        self.write_db_metadata(bundle, database)
        write_json(bundle / 'release.json', release)
        for profile in release['profiles']:
            root = bundle / 'exports' / profile
            manifest = read_json(root / 'manifest.json')
            manifest.update(sources=snapshot['sources'])
            manifest['counts'].update(snapshot['counts'])
            manifest['exclusions'].update(snapshot['exclusions'])
            write_json(root / 'manifest.json', manifest)
            self.reseal_export(bundle, profile)
        self.reseal(bundle)

    def test_every_manifest_field_has_explicit_shape_and_type_checks(self):
        _, bundle = self.fixture()
        manifests = {'release': read_json(bundle / 'release.json'), 'snapshot': read_json(bundle / 'snapshot.json'),
                     'export': read_json(bundle / 'exports/full/manifest.json'), 'database': self.db_metadata(bundle)}
        probes = 0
        for kind, original in manifests.items():
            validate_contract(kind, original)
            for path, operation, value in mutations(original):
                # Dynamic maps are constrained by bundle layout, not the object rule.
                if kind == 'release' and len(path) == 2 and path[0] in ('artifacts', 'profiles'):
                    continue
                with self.subTest(kind=kind, path=path, operation=operation, value=value):
                    with self.assertRaises(DataError):
                        validate_contract(kind, changed(original, path, operation, value))
                probes += 1
        self.assertGreater(probes, 1000)
        print(f'\nManifest contract: {probes} structural/type mutations checked', flush=True)

    def test_recursive_snapshot_mutations_fail_at_snapshot_load_boundary(self):
        prepared, bundle = self.fixture()
        path = self.store / 'snapshots' / prepared['snapshot_version'] / 'snapshot.json'
        original = read_json(path)
        for location, operation, value in mutations(original):
            with self.subTest(path=location, operation=operation):
                write_json(path, changed(original, location, operation, value))
                with self.assertRaises((DataError, ValueError, TypeError, KeyError)):
                    load_snapshot(self.store, prepared['snapshot_version'], verify=False)
        write_json(path, original)
        self.assertEqual(self.prepare('--selected-limit', 1, offline=prepared['snapshot_version']), prepared)

    def test_resealed_source_metadata_requires_complete_typed_provenance(self):
        _, original = self.fixture()
        for case in ('missing', 'values', 'counts'):
            bundle = self.directory / case
            shutil.copytree(original, bundle)
            snapshot = read_json(bundle / 'snapshot.json')
            if case == 'missing':
                snapshot['sources'] = {k: {field: v[field] for field in ('sha256', 'bytes', 'decoded')}
                                       for k, v in snapshot['sources'].items()}
            elif case == 'values':
                for info in snapshot['sources'].values():
                    info.update(url=[], acquisition=False, retrieved_at='impossible', compression='zip',
                                content_length=-42, etag={})
            else:
                snapshot['counts'].update(numbered_orbits=0, unnumbered_orbits=snapshot['counts']['orbital_records'])
            self.sync_snapshot(bundle, snapshot)
            before = self.tree(bundle)
            self.verify(bundle, code=1)
            self.assertEqual(self.tree(bundle), before)

    def test_recursive_database_metadata_mutations_fail_both_readers(self):
        _, bundle = self.fixture()
        path = bundle / 'orrery.sqlite3'
        original_bytes = path.read_bytes()
        original = self.db_metadata(bundle)
        for location, operation, value in mutations(original):
            # metadata's outer container is a SQL key/value table, not JSON.
            if not location:
                continue
            with self.subTest(path=location, operation=operation):
                self.write_db_metadata(bundle, changed(original, location, operation, value))
                before = path.read_bytes()
                for reader in (database_info, query_database):
                    with self.assertRaises((DataError, ValueError, TypeError, KeyError)):
                        reader(path)
                self.assertEqual(path.read_bytes(), before)
                path.write_bytes(original_bytes)

    def test_recursive_export_mutations_cannot_activate_cached_outputs(self):
        prepared, _ = self.fixture()
        root = self.directory / 'exports'
        result = export(self.store, root, prepared['snapshot_version'], 2)
        path = Path(result['path']) / 'manifest.json'
        original = read_json(path)
        (root / 'latest.json').unlink()
        for location, operation, value in mutations(original):
            with self.subTest(path=location, operation=operation):
                write_json(path, changed(original, location, operation, value))
                with self.assertRaises((DataError, ValueError, TypeError, KeyError)):
                    export(self.store, root, prepared['snapshot_version'], 2)
                self.assertFalse((root / 'latest.json').exists())
        write_json(path, original)
        self.assertEqual(export(self.store, root, prepared['snapshot_version'], 2), result)

    def test_discovery_ties_preserve_source_order(self):
        script = ("import orrery_data.pipeline as p\nfrom orrery_data.cli import main\n"
                  "original=p.master_rows\n"
                  "def rows(*args, **kwargs):\n"
                  " for row in original(*args, **kwargs):\n"
                  "  if row['disc'] is not None: row['disc']=2451544.5\n"
                  "  yield row\np.master_rows=rows\nraise SystemExit(main())")
        prepared = self.prepare(script=script)
        bundle = Path(prepared['path'])
        self.verify(bundle)
        root = bundle / 'exports/full'
        rows = read_json(root / 'catalog.json')
        rows.reverse()
        payload = (json.dumps(rows) + '\n').encode()
        (root / 'catalog.json').write_bytes(payload)
        (root / 'catalog.json.gz').write_bytes(gzip.compress(payload, mtime=0))
        self.reseal_export(bundle, 'full')
        self.assertIn('differs from selected master', self.verify(bundle, code=1)['error'])

    def test_decoded_source_identity_is_checked_against_stored_bytes(self):
        prepared, _ = self.fixture()
        original = self.store / 'snapshots' / prepared['snapshot_version']
        snapshot = read_json(original / 'snapshot.json')
        snapshot['sources']['mpcorb']['decoded']['sha256'] = 'f' * 64
        snapshot['identity']['sources']['mpcorb'] = 'f' * 64
        version = 'snapshot-v1-' + digest(snapshot['identity'])
        snapshot['snapshot_version'] = version
        copied = self.store / 'snapshots' / version
        shutil.copytree(original, copied)
        write_json(copied / 'snapshot.json', snapshot)
        before = self.tree(self.store)
        with self.assertRaisesRegex(DataError, 'source.*mismatch'):
            load_snapshot(self.store, version)
        self.assertEqual(self.tree(self.store), before)

    def test_catalogs_must_match_source_selection_values_and_tie_order(self):
        _, original = self.fixture()
        for case in ('orbit', 'selection', 'tie'):
            bundle = self.directory / case
            shutil.copytree(original, bundle)
            profile = 'first-1' if case == 'selection' else 'full'
            root = bundle / 'exports' / profile
            rows = read_json(root / 'catalog.json')
            if case == 'orbit':
                rows[0]['a'] += 1
            elif case == 'selection':
                rows = read_json(bundle / 'exports/full/catalog.json')[-1:]
            else:
                # Fixture Ceres/Pallas dates differ: make an internally sorted
                # but wrong catalog by duplicating a plausible row in place.
                rows[1] = dict(rows[0])
            payload = (json.dumps(rows) + '\n').encode()
            (root / 'catalog.json').write_bytes(payload)
            (root / 'catalog.json.gz').write_bytes(gzip.compress(payload, mtime=0))
            self.reseal_export(bundle, profile)
            before = self.tree(bundle)
            error = self.verify(bundle, code=1)['error']
            self.assertIn('differs from selected master', error)
            self.assertEqual(self.tree(bundle), before)

    def test_sqlite_schema_and_every_row_must_match_master(self):
        _, original = self.fixture()
        for case, sql in [
            ('text-orbit', "UPDATE orbits SET a='broken' WHERE source_order=1"),
            ('id', "UPDATE objects SET id='mpc:number:999999' WHERE source_order=1"),
            ('numeric-orbit', 'UPDATE orbits SET a=a+1 WHERE source_order=1'),
            ('schema', 'ALTER TABLE orbits RENAME TO old_orbits; CREATE TABLE orbits AS SELECT * FROM old_orbits; DROP TABLE old_orbits'),
        ]:
            bundle = self.directory / case
            shutil.copytree(original, bundle)
            with sqlite3.connect(bundle / 'orrery.sqlite3') as db:
                db.executescript(sql)
            self.reseal(bundle)
            before = self.tree(bundle)
            self.verify(bundle, code=1)
            self.assertEqual(self.tree(bundle), before)

    def test_even_consistently_resealed_master_must_be_readable(self):
        _, original = self.fixture()
        for case, payload in [('gzip', b'not gzip or JSON'), ('record', gzip.compress(b'{"id":"missing fields"}\n'))]:
            bundle = self.directory / case
            shutil.copytree(original, bundle)
            snapshot = read_json(bundle / 'snapshot.json')
            for root in (bundle / 'exports').iterdir():
                (root / 'master.jsonl.gz').write_bytes(payload)
                self.reseal_export(bundle, root.name)
            snapshot['files']['master.jsonl.gz'] = file_info(bundle / 'exports/full/master.jsonl.gz')
            self.sync_snapshot(bundle, snapshot)
            self.assertIn('master', self.verify(bundle, code=1)['error'].lower())

    def test_boolean_profiles_and_extended_database_identity_reject(self):
        _, original = self.fixture()
        for case in ('records', 'limit', 'database'):
            bundle = self.directory / case
            shutil.copytree(original, bundle)
            release = read_json(bundle / 'release.json')
            if case == 'database':
                database = self.db_metadata(bundle)
                database['identity']['extra'] = 1
                database['database_version'] = 'sqlite-v1-' + digest(database['identity'])
                release['database_version'] = database['database_version']
                self.write_db_metadata(bundle, database)
            elif case == 'limit':
                release['profiles']['first-1']['selection']['limit'] = True
            else:
                release['profiles']['first-1']['records'] = True
            write_json(bundle / 'release.json', release)
            self.reseal(bundle)
            self.verify(bundle, code=1)

    def test_strict_json_and_unexpected_empty_directories_reject(self):
        for payload in ('{"x":1,"x":2}', '{"x":NaN}', '{"x":Infinity}', '{"x":1e999}'):
            with self.assertRaises(DataError):
                loads_json(payload)
        _, bundle = self.fixture()
        payload = (bundle / 'release.json').read_text()
        (bundle / 'release.json').write_text(payload.replace('"release_schema_version":1', '"release_schema_version":1,"release_schema_version":1', 1))
        self.assertIn('Duplicate JSON', self.verify(bundle, code=1)['error'])
        (bundle / 'release.json').write_text(payload)
        (bundle / 'exports/first-999').mkdir()
        self.assertIn('directory inventory', self.verify(bundle, code=1)['error'])

    def test_export_reuse_does_not_activate_resealed_false_metadata(self):
        prepared, _ = self.fixture()
        root = self.directory / 'exports'
        result = export(self.store, root, prepared['snapshot_version'], 2)
        manifest_path = Path(result['path']) / 'manifest.json'
        manifest = read_json(manifest_path)
        manifest['compression']['master']['zlib'] = '9.9.9'
        write_json(manifest_path, manifest)
        (root / 'latest.json').unlink()
        with self.assertRaisesRegex(DataError, 'snapshot provenance'):
            export(self.store, root, prepared['snapshot_version'], 2)
        self.assertFalse((root / 'latest.json').exists())

    def test_invalid_api_options_fail_before_output(self):
        for limits in ([True], [1.0], ['1'], [], {}, 2):
            with self.subTest(limits=limits):
                with self.assertRaises(DataError):
                    prepare_release(self.store, self.output, '1' * 40, limits=limits)
                self.assertFalse(self.store.exists())
                self.assertFalse(self.output.exists())
