"""Systematic contract mutations plus independently resealed semantic failures."""

import copy
import gzip
import io
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


def fields(names, **nested):
    return {**dict.fromkeys(names.split()), **nested}


# Test-owned schema-1 field inventory, transcribed from the published contract.
# Never derive this from a producer fixture or import production schema objects:
# a field removed from both the producer and validator must still fail here.
FILE_INVENTORY = fields('sha256 bytes')
COUNT_INVENTORY = fields('orbital_records master_records known_discovery missing_discovery '
                         'numbered_orbits unnumbered_orbits unsupported_orbits '
                         'discovery_records unmatched_discovery_records')
BASELINE_INVENTORY = fields('orbital_records discovery_records master_records known_discovery')
SOURCE_INVENTORY = fields('url retrieved_at acquisition etag last_modified content_length '
                          'sha256 bytes compression resolved_url', decoded=FILE_INVENTORY)
SOURCES_INVENTORY = {'mpcorb': SOURCE_INVENTORY, 'numbered': SOURCE_INVENTORY}
SOURCE_HASH_INVENTORY = fields('mpcorb numbered')
COMPRESSION_INVENTORY = fields('format level mtime zlib')
SELECTION_INVENTORY = fields('profile limit select sort')
SNAPSHOT_FILES_INVENTORY = {name: FILE_INVENTORY for name in ('master.jsonl.gz', 'MPCORB-header.txt')}
EXCLUSIONS_INVENTORY = {'master': fields('non_elliptic_orbits'),
                        'discovery': fields('missing_discovery_date')}
SNAPSHOT_INVENTORY = fields('snapshot_version schema_version tool_version created_at',
    identity=fields('schema_version tool_version', sources=SOURCE_HASH_INVENTORY),
    sources=SOURCES_INVENTORY, counts=COUNT_INVENTORY, files=SNAPSHOT_FILES_INVENTORY,
    exclusions=EXCLUSIONS_INVENTORY, compression=COMPRESSION_INVENTORY)
EXPORT_ARTIFACT_INVENTORY = {
    **{name: fields('sha256 bytes profile records') for name in
       ('master.jsonl.gz', 'catalog.json', 'catalog.json.gz')},
    **{name: FILE_INVENTORY for name in ('MPCORB-header.txt', 'NOTICE.txt')},
}
MANIFEST_INVENTORIES = {
    'snapshot': SNAPSHOT_INVENTORY,
    'export': fields('data_version snapshot_version schema_version tool_version created_at',
        identity=fields('snapshot_version tool_version schema_version', selection=SELECTION_INVENTORY),
        selection=SELECTION_INVENTORY, sources=SOURCES_INVENTORY,
        counts={**COUNT_INVENTORY, 'discovery_export': None},
        exclusions={**EXCLUSIONS_INVENTORY, 'selection_limit': None},
        compression={'master': COMPRESSION_INVENTORY, 'catalog': COMPRESSION_INVENTORY},
        artifacts=EXPORT_ARTIFACT_INVENTORY),
    'database': fields('database_version database_schema_version tool_version sqlite_version created_at '
                       'mpcorb_header notice',
        identity=fields('database_schema_version tool_version snapshot_version notice_sha256',
                        files=SNAPSHOT_FILES_INVENTORY), snapshot=SNAPSHOT_INVENTORY),
    'release': fields('release_version release_schema_version created_at database_version',
        identity=fields('release_schema_version dataset_version snapshot_version json_schema_version '
                         'database_schema_version selected_limits notice_sha256',
                         producer=fields('commit tool_version')),
        dataset_identity={'sources': SOURCE_HASH_INVENTORY}, runtime=fields('python sqlite zlib'),
        sources=SOURCES_INVENTORY, counts=COUNT_INVENTORY,
        profiles={'*': fields('data_version records', selection=SELECTION_INVENTORY)},
        preparation=fields('allow_count_decrease', baseline_counts=BASELINE_INVENTORY,
                           previous_counts=COUNT_INVENTORY), artifacts={'*': FILE_INVENTORY}),
}


def mutations(value, path=()):
    """Enumerate malformed structures independently of the schema implementation."""
    if isinstance(value, dict):
        yield path, 'replace', []
        yield path + ('unexpected_schema_extension',), 'replace', 1
        for key, child in value.items():
            if key != 'resolved_url' or value['acquisition'] == 'http':
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

    def assert_inventory(self, value, expected, path=()):
        if expected is None:
            return
        if value is None and path in (('preparation', 'baseline_counts'), ('preparation', 'previous_counts')):
            return
        self.assertIsInstance(value, dict, path)
        if set(expected) == {'*'}:
            self.assertTrue(value, path)
            for key, child in value.items():
                self.assert_inventory(child, expected['*'], path + (key,))
            return
        keys = set(expected)
        if 'resolved_url' in keys and value.get('acquisition') == 'local' and 'resolved_url' not in value:
            keys.remove('resolved_url')
        self.assertEqual(set(value), keys, path)
        for key, child in value.items():
            self.assert_inventory(child, expected[key], path + (key,))

    def release_variants(self, bundle):
        original = read_json(bundle / 'release.json')
        self.assertIsNone(original['preparation']['baseline_counts'])
        self.assertIsNone(original['preparation']['previous_counts'])
        populated = copy.deepcopy(original)
        populated['preparation']['baseline_counts'] = {
            key: original['counts'][key] for key in BASELINE_INVENTORY}
        populated['preparation']['previous_counts'] = dict(original['counts'])
        return [('null-preparation', original), ('populated-preparation', populated)]

    def write_release_manifest(self, bundle, manifest, artifact_names):
        # Keep deliberate metadata mutations intact, including missing/extra
        # artifact map entries. Only reseal bytes at the transport boundary.
        write_json(bundle / 'release.json', manifest)
        (bundle / 'SHA256SUMS').write_text(''.join(
            f"{file_info(bundle / name)['sha256']}  {name}\n"
            for name in sorted([*artifact_names, 'release.json'])))

    def verification_state(self, bundle):
        # Include directories and modification times: unchanged file hashes
        # alone would miss empty-directory creation or rewriting identical bytes.
        return {str(path.relative_to(bundle)): (path.stat().st_mode, path.stat().st_mtime_ns,
                                                path.read_bytes() if path.is_file() else None)
                for path in (bundle, *bundle.rglob('*'))}

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
        manifests = [('release', label, value) for label, value in self.release_variants(bundle)]
        manifests += [('snapshot', 'http', read_json(bundle / 'snapshot.json')),
                      ('export', 'full', read_json(bundle / 'exports/full/manifest.json')),
                      ('export', 'selected', read_json(bundle / 'exports/first-1/manifest.json')),
                      ('database', 'http', self.db_metadata(bundle))]
        probes = 0
        for kind, label, original in manifests:
            self.assert_inventory(original, MANIFEST_INVENTORIES[kind])
            if kind == 'release':
                self.assertEqual(set(original['profiles']), {'full', 'first-1', 'first-2'})
                self.assertEqual(set(original['artifacts']),
                    {'snapshot.json', 'NOTICE.txt', 'MPCORB-header.txt', 'orrery.sqlite3'} |
                    {f'exports/{profile}/{name}' for profile in ('full', 'first-1', 'first-2')
                     for name in (*EXPORT_ARTIFACT_INVENTORY, 'manifest.json', 'SHA256SUMS')})
            if kind == 'export':
                self.assertEqual(original['selection']['limit'], None if label == 'full' else 1)
            validate_contract(kind, original)
            for path, operation, value in mutations(original):
                # Dynamic maps are constrained by bundle layout, not the object rule.
                if kind == 'release' and len(path) == 2 and path[0] in ('artifacts', 'profiles'):
                    continue
                with self.subTest(kind=kind, fixture=label, path=path, operation=operation, value=value):
                    with self.assertRaises(DataError):
                        validate_contract(kind, changed(original, path, operation, value))
                probes += 1
        print(f'\nDirect schema checks: {probes} structural/type mutations checked', flush=True)

    def test_recursive_release_mutations_fail_readonly_file_verification(self):
        _, bundle = self.fixture()
        variants = self.release_variants(bundle)
        artifact_names = set(variants[0][1]['artifacts'])
        probes = 0
        for label, original in variants:
            self.write_release_manifest(bundle, original, artifact_names)
            before = self.verification_state(bundle)
            self.assertEqual(verify_release(bundle)['status'], 'verified')
            self.assertEqual(self.verification_state(bundle), before)
            for location, operation, value in mutations(original):
                with self.subTest(fixture=label, path=location, operation=operation, value=value):
                    malformed = changed(original, location, operation, value)
                    if location and location[0] == 'identity' and 'identity' in malformed:
                        # A stale identity hash must not conceal a missing
                        # schema/type/relationship check inside that identity.
                        malformed['release_version'] = 'release-v1-' + digest(malformed['identity'])
                    self.write_release_manifest(bundle, malformed, artifact_names)
                    before = self.verification_state(bundle)
                    with self.assertRaises(DataError):
                        verify_release(bundle)
                    self.assertEqual(self.verification_state(bundle), before)
                probes += 1
        self.write_release_manifest(bundle, variants[0][1], artifact_names)
        self.assertEqual(verify_release(bundle)['status'], 'verified')
        print(f'\nRelease file boundary: {probes} resealed structural/type mutations checked', flush=True)

    def test_http_requires_resolved_url_while_local_acquisition_may_omit_it(self):
        _, bundle = self.fixture()
        snapshot = read_json(bundle / 'snapshot.json')
        for source in ('mpcorb', 'numbered'):
            self.assertEqual(snapshot['sources'][source]['acquisition'], 'http')
            self.assertIn('resolved_url', snapshot['sources'][source])
            malformed = copy.deepcopy(snapshot)
            del malformed['sources'][source]['resolved_url']
            with self.assertRaisesRegex(DataError, 'requires retrieval time and resolved URL'):
                validate_contract('snapshot', malformed)
        local = self.cli('prepare-release', '--store', self.directory / 'local-store',
                         '--output', self.directory / 'local-release', '--producer-commit', '1' * 40,
                         '--mpcorb', test_releases.ROOT / 'tests/fixtures/MPCORB.DAT',
                         '--numbered', test_releases.ROOT / 'tests/fixtures/NumberedMPs.txt')
        local_bundle = Path(local['path'])
        for kind, original in (
            ('snapshot', read_json(local_bundle / 'snapshot.json')),
            ('export', read_json(local_bundle / 'exports/full/manifest.json')),
            ('database', self.db_metadata(local_bundle)),
            ('release', read_json(local_bundle / 'release.json')),
        ):
            self.assert_inventory(original, MANIFEST_INVENTORIES[kind])
            sources = original['snapshot']['sources'] if kind == 'database' else original['sources']
            for source in sources.values():
                self.assertEqual(source['acquisition'], 'local')
                self.assertNotIn('resolved_url', source)
            validate_contract(kind, original)
        before = self.verification_state(local_bundle)
        self.assertEqual(verify_release(local_bundle)['status'], 'verified')
        self.assertEqual(self.verification_state(local_bundle), before)

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
        root = self.directory / 'standalone-exports'
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
        for case, payload in [('gzip', b'not gzip or JSON'), ('record', gzip.compress(b'{"id":"missing fields"}\n', mtime=0))]:
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
        root = self.directory / 'standalone-exports'
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

    def test_http_length_reconciles_raw_bytes_while_local_provenance_is_preserved(self):
        _, bundle = self.fixture()
        snapshot = read_json(bundle / 'snapshot.json')
        snapshot['sources']['mpcorb']['content_length'] = '1'
        self.sync_snapshot(bundle, snapshot)
        self.assertIn('Content-Length must match raw bytes', self.verify(bundle, code=1)['error'])
        snapshot['sources']['mpcorb']['acquisition'] = 'local'
        self.sync_snapshot(bundle, snapshot)
        self.verify(bundle)

    def test_generated_catalog_headers_match_observable_compression_metadata(self):
        prepared, original = self.fixture()
        for case in ('mtime', 'filename'):
            bundle = self.directory / case
            shutil.copytree(original, bundle)
            root = bundle / 'exports/full'
            plain = (root / 'catalog.json').read_bytes()
            stream = io.BytesIO()
            with gzip.GzipFile(fileobj=stream, mode='wb', filename='catalog.json' if case == 'filename' else '',
                               mtime=1234567 if case == 'mtime' else 0, compresslevel=6) as gz:
                gz.write(plain)
            payload = stream.getvalue()
            self.assertEqual(gzip.decompress(payload), plain)
            (root / 'catalog.json.gz').write_bytes(payload)
            self.reseal_export(bundle, 'full')
            before = self.tree(bundle)
            self.assertIn('generated gzip', self.verify(bundle, code=1)['error'])
            self.assertEqual(self.tree(bundle), before)
            output = self.directory / ('cached-' + case)
            result = self.cli('export', '--store', self.store, '--output', output,
                              '--snapshot', prepared['snapshot_version'])
            cached = Path(result['path'])
            (cached / 'catalog.json.gz').write_bytes(payload)
            manifest = read_json(cached / 'manifest.json')
            manifest['artifacts']['catalog.json.gz'].update(file_info(cached / 'catalog.json.gz'))
            write_json(cached / 'manifest.json', manifest)
            (cached / 'SHA256SUMS').write_text(''.join(
                f"{file_info(cached / name)['sha256']}  {name}\n"
                for name in sorted([*manifest['artifacts'], 'manifest.json'])))
            before = self.tree(output)
            self.assertIn('generated gzip', self.cli('export', '--store', self.store, '--output', output, code=1)['error'])
            self.assertEqual(self.tree(output), before)

    def test_resealed_master_and_database_cannot_disagree_with_displayed_number(self):
        _, bundle = self.fixture()
        master = bundle / 'exports/full/master.jsonl.gz'
        rows = [json.loads(line) for line in gzip.decompress(master.read_bytes()).decode().splitlines()]
        rows[0]['readable_designation'] = '(999999) Ceres'
        payload = gzip.compress((''.join(json.dumps(row) + '\n' for row in rows)).encode(), mtime=0)
        for root in (bundle / 'exports').iterdir():
            (root / 'master.jsonl.gz').write_bytes(payload)
        with sqlite3.connect(bundle / 'orrery.sqlite3') as db:
            db.execute("UPDATE objects SET readable_designation='(999999) Ceres' WHERE source_order=1")
        snapshot = read_json(bundle / 'snapshot.json')
        snapshot['files']['master.jsonl.gz'] = file_info(master)
        self.sync_snapshot(bundle, snapshot)
        self.assertIn('Packed and readable MPC numbers disagree', self.verify(bundle, code=1)['error'])

    def test_resealed_catalog_and_master_reject_additional_gzip_members(self):
        _, original = self.fixture()
        for artifact in ('catalog', 'master'):
            for mtime in (0, 1234567):
                bundle = self.directory / f'{artifact}-{mtime}'
                shutil.copytree(original, bundle)
                path = bundle / f'exports/full/{artifact}.json{"l" if artifact == "master" else ""}.gz'
                original_payload = gzip.decompress(path.read_bytes())
                payload = path.read_bytes() + gzip.compress(b'', mtime=mtime)
                self.assertEqual(gzip.decompress(payload), original_payload)
                if artifact == 'catalog':
                    path.write_bytes(payload)
                    self.reseal_export(bundle, 'full')
                else:
                    for profile in (bundle / 'exports').iterdir():
                        (profile / 'master.jsonl.gz').write_bytes(payload)
                    snapshot = read_json(bundle / 'snapshot.json')
                    snapshot['files']['master.jsonl.gz'] = file_info(path)
                    self.sync_snapshot(bundle, snapshot)
                before = self.verification_state(bundle)
                self.assertIn('exactly one member', self.verify(bundle, code=1)['error'])
                self.assertEqual(self.verification_state(bundle), before)

    def test_stored_master_and_cached_catalog_reject_additional_gzip_members(self):
        prepared, _ = self.fixture()
        root = self.directory / 'standalone-exports'
        exported = self.cli('export', '--store', self.store, '--output', root)
        cached = Path(exported['path'])
        catalog = cached / 'catalog.json.gz'
        catalog.write_bytes(catalog.read_bytes() + gzip.compress(b'', mtime=123))
        manifest = read_json(cached / 'manifest.json')
        manifest['artifacts']['catalog.json.gz'].update(file_info(catalog))
        write_json(cached / 'manifest.json', manifest)
        (cached / 'SHA256SUMS').write_text(''.join(
            f"{file_info(cached / name)['sha256']}  {name}\n"
            for name in sorted([*manifest['artifacts'], 'manifest.json'])))
        before = self.tree(root)
        self.assertIn('exactly one member', self.cli('export', '--store', self.store, '--output', root, code=1)['error'])
        self.assertEqual(self.tree(root), before)
        source = self.store / 'snapshots' / prepared['snapshot_version']
        master = source / 'master.jsonl.gz'
        master.write_bytes(master.read_bytes() + gzip.compress(b'', mtime=123))
        snapshot = read_json(source / 'snapshot.json')
        snapshot['files']['master.jsonl.gz'] = file_info(master)
        write_json(source / 'snapshot.json', snapshot)
        for command, destination_flag, name in (
                ('export', '--output', 'fresh-export'), ('build-db', '--database', 'new.sqlite3'),
                ('prepare-release', '--output', 'fresh-release')):
            options = ['--producer-commit', '1' * 40] if command == 'prepare-release' else []
            destination = self.directory / name
            error = self.cli(command, '--store', self.store, '--snapshot', prepared['snapshot_version'],
                             destination_flag, destination, *options, code=1)['error']
            self.assertIn('exactly one member', error)
            self.assertFalse((destination / 'latest.json').exists())
            if command == 'build-db': self.assertFalse(destination.exists())

    def test_refresh_cannot_reactivate_cached_master_with_additional_members(self):
        prepared, _ = self.fixture()
        source = self.store / 'snapshots' / prepared['snapshot_version']
        master = source / 'master.jsonl.gz'
        original_bytes = master.read_bytes()
        snapshot = read_json(source / 'snapshot.json')
        original_manifest = copy.deepcopy(snapshot)
        master.write_bytes(original_bytes + gzip.compress(b'', mtime=123))
        snapshot['files']['master.jsonl.gz'] = file_info(master)
        write_json(source / 'snapshot.json', snapshot)
        (self.store / 'current.json').unlink()
        before = self.tree(self.store)
        with self.assertRaisesRegex(DataError, 'exactly one member'):
            load_snapshot(self.store, prepared['snapshot_version'])
        options = ['--mpcorb', test_releases.ROOT / 'tests/fixtures/MPCORB.DAT',
                   '--numbered', test_releases.ROOT / 'tests/fixtures/NumberedMPs.txt']
        self.assertIn('exactly one member', self.cli('refresh', '--store', self.store, *options, code=1)['error'])
        self.assertEqual(self.tree(self.store), before)
        self.assertFalse((self.store / 'current.json').exists())
        master.write_bytes(original_bytes)
        write_json(source / 'snapshot.json', original_manifest)
        refreshed = self.cli('refresh', '--store', self.store, *options)
        self.assertEqual(refreshed['snapshot_version'], prepared['snapshot_version'])
        self.assertEqual(read_json(self.store / 'current.json'), {'snapshot_version': prepared['snapshot_version']})
