"""Persisted candidate structures must be checked before content reads/reuse."""

from contextlib import contextmanager
import hashlib
import os
from pathlib import Path
import stat
import unittest

import test_releases
from orrery_data.pipeline import load_snapshot
from orrery_data.storage import read_json


def state(root):
    """Capture files and links without opening special files or following links."""
    result = {}
    for directory, dirs, files in os.walk(root, followlinks=False):
        for name in dirs + files:
            path = Path(directory) / name
            mode = path.lstat().st_mode
            value = os.readlink(path) if stat.S_ISLNK(mode) else None
            if stat.S_ISREG(mode):
                value = hashlib.sha256(path.read_bytes()).hexdigest()
            result[str(path.relative_to(root))] = (mode, value)
    return result


class CandidateInventory(unittest.TestCase):
    setUpClass = classmethod(test_releases.ReleaseCLI.setUpClass.__func__)
    tearDownClass = classmethod(test_releases.ReleaseCLI.tearDownClass.__func__)
    setUp = test_releases.ReleaseCLI.setUp
    cli = test_releases.ReleaseCLI.cli

    def local_args(self):
        return ['--mpcorb', test_releases.ROOT / 'tests/fixtures/MPCORB.DAT',
                '--numbered', test_releases.ROOT / 'tests/fixtures/NumberedMPs.txt']

    def refresh(self, **kwargs):
        return self.cli('refresh', '--store', self.store, *self.local_args(), **kwargs)

    @contextmanager
    def replace(self, target, kind):
        saved = self.directory / 'saved-entry'
        target.rename(saved)
        if kind == 'symlink':
            target.symlink_to(saved, target_is_directory=saved.is_dir())
        elif kind == 'dangling':
            target.symlink_to(self.directory / 'absent-target')
        elif kind == 'directory':
            target.mkdir()
        elif kind == 'fifo':
            os.mkfifo(target)
        else:
            target.write_text('not a directory')
        try:
            yield
        finally:
            if target.is_dir() and not target.is_symlink():
                target.rmdir()
            else:
                target.unlink()
            saved.rename(target)

    def test_cached_export_inventory_rejects_before_reads_and_preserves_pointer(self):
        self.refresh()
        full = self.cli('export', '--store', self.store, '--output', self.output)
        target = Path(full['path'])
        previous = self.cli('export', '--store', self.store, '--output', self.output, '--limit', 2)
        entries = ('manifest.json', 'SHA256SUMS', 'master.jsonl.gz', 'catalog.json',
                   'catalog.json.gz', 'MPCORB-header.txt', 'NOTICE.txt')
        variants = [(target, kind) for kind in ('symlink', 'dangling', 'file')]
        variants += [(target / name, kind) for name in entries
                     for kind in ('symlink', 'dangling', 'directory', 'fifo')]
        for path, kind in variants:
            with self.subTest(path=path.name, kind=kind), self.replace(path, kind):
                before = state(self.directory)
                error = self.cli('export', '--store', self.store, '--output', self.output, code=1)['error']
                self.assertIn('Export', error)
                self.assertEqual(state(self.directory), before)
                self.assertEqual(read_json(self.output / 'latest.json'), {'data_version': previous['data_version']})
        for kind in ('file', 'directory'):
            extra = target / 'unexpected'
            extra.mkdir() if kind == 'directory' else extra.write_text('extra')
            before = state(self.directory)
            self.assertIn('inventory', self.cli('export', '--store', self.store, '--output', self.output, code=1)['error'])
            self.assertEqual(state(self.directory), before)
            extra.rmdir() if kind == 'directory' else extra.unlink()
        self.assertEqual(self.cli('export', '--store', self.store, '--output', self.output), full)

    def test_snapshot_inventory_is_checked_at_all_consumers_and_cache_activation(self):
        snapshot = self.refresh()
        version = snapshot['snapshot_version']
        target = self.store / 'snapshots' / version
        entries = ('snapshot.json', 'mpcorb.input', 'numbered.input', 'master.jsonl.gz', 'MPCORB-header.txt')
        variants = [(target, kind) for kind in ('symlink', 'dangling', 'file')]
        variants += [(target / name, kind) for name in entries
                     for kind in ('symlink', 'dangling', 'directory', 'fifo')]
        for path, kind in variants:
            with self.subTest(path=path.name, kind=kind), self.replace(path, kind):
                before = state(self.directory)
                # Includes metadata-only loading used by the check command.
                for verify in (False, True):
                    with self.assertRaisesRegex(ValueError, 'Snapshot'):
                        load_snapshot(self.store, version, verify=verify)
                self.assertIn('Snapshot', self.refresh(code=1)['error'])
                self.assertEqual(state(self.directory), before)
        for kind in ('file', 'directory'):
            extra = target / 'unexpected'
            extra.mkdir() if kind == 'directory' else extra.write_text('extra')
            with self.assertRaisesRegex(ValueError, 'inventory'):
                load_snapshot(self.store, version)
            extra.rmdir() if kind == 'directory' else extra.unlink()
        with self.replace(target, 'symlink'):
            (self.store / 'current.json').unlink()
            before = state(self.directory)
            self.assertIn('Snapshot', self.refresh(code=1)['error'])
            self.assertEqual(state(self.directory), before)
            self.assertFalse((self.store / 'current.json').exists())
        self.assertEqual(self.refresh()['snapshot_version'], version)
        commands = [('export', '--output', self.output),
                    ('build-db', '--database', self.directory / 'database.sqlite3'),
                    ('prepare-release', '--output', self.output, '--producer-commit', test_releases.COMMIT)]
        with self.replace(target / 'master.jsonl.gz', 'symlink'):
            for command, *args in commands:
                before = state(self.directory)
                error = self.cli(command, '--store', self.store, '--snapshot', version, *args, code=1)['error']
                self.assertIn('Snapshot', error)
                # prepare-release may create its own writer lock, but no candidate/pointer.
                after = state(self.directory)
                after.pop(str((self.output / '.lock').relative_to(self.directory)), None)
                after.pop(str(self.output.relative_to(self.directory)), None)
                before.pop(str((self.output / '.lock').relative_to(self.directory)), None)
                before.pop(str(self.output.relative_to(self.directory)), None)
                self.assertEqual(after, before)
        prepared = self.cli('prepare-release', '--store', self.store, '--snapshot', version,
                            '--output', self.output, '--producer-commit', test_releases.COMMIT)
        self.cli('verify-release', '--bundle', prepared['path'])

    def test_dangling_pointers_cannot_remove_snapshot_or_release_baselines(self):
        snapshot = self.refresh()
        pointer = self.store / 'current.json'
        with self.replace(pointer, 'dangling'):
            before = state(self.directory)
            self.refresh(code=1)
            self.assertEqual(state(self.directory), before)
        prepared = self.cli('prepare-release', '--store', self.store, '--snapshot', snapshot['snapshot_version'],
                            '--output', self.output, '--producer-commit', test_releases.COMMIT)
        pointer = self.output / 'latest.json'
        with self.replace(pointer, 'dangling'):
            before = state(self.directory)
            self.cli('prepare-release', '--store', self.store, '--snapshot', snapshot['snapshot_version'],
                     '--output', self.output, '--producer-commit', test_releases.COMMIT, code=1)
            self.assertEqual(state(self.directory), before)
        self.assertEqual(self.cli('prepare-release', '--store', self.store, '--snapshot', snapshot['snapshot_version'],
                                 '--output', self.output, '--producer-commit', test_releases.COMMIT), prepared)

    def test_invalid_url_ports_reject_before_source_or_release_writes(self):
        invalid = ('http://example.test:not-a-port', 'http://example.test:99999',
                   'https://example.test:-1', 'http://[::1]:65536')
        for url in invalid:
            for command in ('refresh', 'prepare-release'):
                for name in ('mpcorb', 'numbered'):
                    with self.subTest(url=url, command=command, name=name):
                        args = ['--output', self.output, '--producer-commit', test_releases.COMMIT] if command == 'prepare-release' else []
                        result = self.cli(command, '--store', self.store, *args, *self.local_args(),
                                          '--' + name + '-url', url, code=1)
                        self.assertIn('HTTP(S) URL', result['error'])
                        self.assertFalse(self.store.exists())
                        self.assertFalse(self.output.exists())
        # A real HTTP server with an explicit nondefault port still works.
        snapshot = self.cli('refresh', '--store', self.store, *test_releases.ReleaseCLI.http_args(self))
        before = state(self.directory)
        for command in ('refresh', 'prepare-release'):
            args = ['--output', self.output, '--producer-commit', test_releases.COMMIT] if command == 'prepare-release' else []
            self.cli(command, '--store', self.store, *args, *self.local_args(), '--mpcorb-url', invalid[0], code=1)
            self.assertEqual(state(self.directory), before)
        self.assertEqual(self.refresh()['snapshot_version'], snapshot['snapshot_version'])

    def test_reserved_snapshot_root_rejects_case_alias_outputs(self):
        snapshot = self.refresh()
        before = state(self.directory)
        outputs = [('export', '--output', self.store / 'SNAPSHOTS'),
                   ('build-db', '--database', self.store / 'SNAPSHOTS/database.sqlite3'),
                   ('prepare-release', '--output', self.store / 'SNAPSHOTS')]
        for command, flag, destination in outputs:
            extra = ['--producer-commit', test_releases.COMMIT] if command == 'prepare-release' else []
            self.cli(command, '--store', self.store, '--snapshot', snapshot['snapshot_version'],
                     flag, destination, *extra, code=1)
            self.assertEqual(state(self.directory), before)
        # Direction matters: pinned preparation at the store root remains supported.
        self.cli('prepare-release', '--store', self.store, '--snapshot', snapshot['snapshot_version'],
                 '--output', self.store, '--producer-commit', test_releases.COMMIT)
