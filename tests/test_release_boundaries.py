"""Composition boundaries: output ownership, API preflight and complete gzip frames."""

import gzip
import io
from pathlib import Path
import shutil
import unittest
from unittest.mock import patch

import test_releases
from orrery_data.formats import DataError
from orrery_data.releases import prepare_release
from orrery_data.storage import read_json, verify_generated_gzip, write_json


class ReleaseBoundaries(unittest.TestCase):
    setUpClass = classmethod(test_releases.ReleaseCLI.setUpClass.__func__)
    tearDownClass = classmethod(test_releases.ReleaseCLI.tearDownClass.__func__)
    setUp = test_releases.ReleaseCLI.setUp
    cli = test_releases.ReleaseCLI.cli
    prepare = test_releases.ReleaseCLI.prepare
    http_args = test_releases.ReleaseCLI.http_args
    tree = test_releases.ReleaseCLI.tree

    def test_export_and_release_output_pointers_cannot_replace_each_other(self):
        prepared = self.prepare()
        exports = self.directory / 'standalone-exports'
        exported = self.cli('export', '--store', self.store, '--output', exports)
        alias = self.directory / 'release-output-alias'
        alias.symlink_to(self.output, target_is_directory=True)
        for cached in (False, True):
            if cached:
                shutil.copytree(Path(exported['path']), self.output / exported['data_version'])
            for output in (self.output, alias):
                before = self.tree(self.output)
                error = self.cli('export', '--store', self.store, '--output', output, code=1)['error']
                self.assertIn('separate export output root', error)
                self.assertEqual(self.tree(self.output), before)
                self.assertEqual(read_json(self.output / 'latest.json')['release_version'], prepared['release_version'])
        before = self.tree(exports)
        error = self.prepare('--output', exports, offline=prepared['snapshot_version'], code=1)['error']
        self.assertIn('separate release output root', error)
        self.assertEqual(self.tree(exports), before)
        # Each owner still consumes and advances its own root normally.
        self.assertEqual(self.prepare(offline=prepared['snapshot_version']), prepared)
        next_export = self.cli('export', '--store', self.store, '--output', exports, '--limit', 2)
        self.assertEqual(read_json(exports / 'latest.json'), {'data_version': next_export['data_version']})

    def test_export_pointer_requires_exact_supported_shape_before_any_writes(self):
        self.prepare()
        output = self.directory / 'malformed-export-root'
        output.mkdir()
        for value in ([], {}, {'data_version': None}, {'data_version': True}, {'data_version': ''},
                      {'data_version': '../escape'}, {'data_version': 'release-v1-' + 'a' * 64},
                      {'data_version': 'export-v1-' + 'a' * 64, 'extra': 1}):
            with self.subTest(pointer=value):
                write_json(output / 'latest.json', value)
                before = self.tree(output)
                self.cli('export', '--store', self.store, '--output', output, code=1)
                self.assertEqual(self.tree(output), before)
                self.assertFalse((output / '.lock').exists())

    def test_local_source_preflight_is_identical_for_pinned_and_unpinned_preparation(self):
        prepared = self.prepare()
        path = test_releases.ROOT / 'tests/fixtures/MPCORB.DAT'
        cases = [[], {'extra': path}, {'mpcorb': path}, {'numbered': path},
                 {'mpcorb': path, 'numbered': None}, {'mpcorb': False},
                 {'mpcorb': False, 'numbered': False}, {'mpcorb': '', 'numbered': ''},
                 {'mpcorb': 0, 'numbered': 0}, {'mpcorb': [], 'numbered': {}},
                 {'mpcorb': path, 'numbered': True}]
        before = self.tree(self.store)
        for version in (None, prepared['snapshot_version']):
            for index, local in enumerate(cases):
                with self.subTest(version=version, local=local):
                    output = self.directory / f'invalid-{version is None}-{index}'
                    with patch('orrery_data.releases.refresh', side_effect=AssertionError('acquisition started')):
                        with self.assertRaises(DataError):
                            prepare_release(self.store, output, test_releases.COMMIT, version=version, local=local)
                    self.assertFalse(output.exists())
                    self.assertEqual(self.tree(self.store), before)
        for index, local in enumerate((None, {}, {'mpcorb': None, 'numbered': None})):
            result = prepare_release(self.store, self.directory / f'valid-{index}', test_releases.COMMIT,
                                     version=prepared['snapshot_version'], local=local)
            self.assertEqual(result['status'], 'verified')

    def test_generated_gzip_requires_one_complete_member_at_every_read_boundary(self):
        path = self.directory / 'generated.gz'
        valid = gzip.compress(b'payload', mtime=0)
        named = io.BytesIO()
        with gzip.GzipFile(fileobj=named, mode='wb', filename='hidden', mtime=0) as stream:
            stream.write(b'')
        corrupt_crc = bytearray(valid)
        corrupt_crc[-8] ^= 1
        invalid = [valid + gzip.compress(b'', mtime=0), valid + gzip.compress(b'', mtime=123),
                   valid + named.getvalue(), gzip.compress(b'', mtime=0) + valid,
                   valid + b'\0', valid + b'junk', bytes(corrupt_crc)]
        invalid += [valid[:cut] for cut in (0, 1, 9, 10, len(valid) - 8, len(valid) - 1)]
        # Include a member ending exactly at a read boundary: unused_data alone
        # cannot detect its following member. Highly compressible data exercises
        # bounded output/unconsumed input over many iterations.
        for chunk_size in (1, 10, len(valid), 1024 * 1024):
            with patch('orrery_data.storage.CHUNK', chunk_size):
                for payload in (valid, gzip.compress(b'', mtime=0)):
                    path.write_bytes(payload)
                    verify_generated_gzip(path)
                for index, payload in enumerate(invalid):
                    with self.subTest(chunk_size=chunk_size, invalid=index):
                        path.write_bytes(payload)
                        with self.assertRaises(DataError):
                            verify_generated_gzip(path)
        path.write_bytes(gzip.compress(b'x' * (3 * 1024 * 1024), mtime=0))
        verify_generated_gzip(path)
