"""Persisted candidate structures must be checked before content reads/reuse."""

from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
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

    def test_previous_release_inventory_precedes_any_manifest_read(self):
        snapshot = self.refresh()
        options = ['--store', self.store, '--snapshot', snapshot['snapshot_version'],
                   '--output', self.output, '--producer-commit', test_releases.COMMIT]
        prepared = self.cli('prepare-release', *options)
        bundle = Path(prepared['path'])
        for kind in ('fifo', 'symlink', 'dangling', 'directory'):
            with self.subTest(kind=kind), self.replace(bundle / 'release.json', kind):
                before = state(self.directory)
                for command in [('verify-release', '--bundle', bundle), ('prepare-release', *options)]:
                    self.cli(*command, code=1)
                    self.assertEqual(state(self.directory), before)
        self.assertEqual(self.cli('prepare-release', *options), prepared)

    def test_mutable_pointers_reject_special_files_and_keep_healthy_link_targets(self):
        snapshot = self.refresh()
        exports = self.directory / 'standalone-exports'
        self.cli('export', '--store', self.store, '--output', exports)
        options = ['--store', self.store, '--snapshot', snapshot['snapshot_version'],
                   '--output', self.output, '--producer-commit', test_releases.COMMIT]
        self.cli('prepare-release', *options)
        cases = [(self.store / 'current.json', ['refresh', '--store', self.store, *self.local_args()]),
                 (exports / 'latest.json', ['export', '--store', self.store, '--output', exports]),
                 (self.output / 'latest.json', ['prepare-release', *options])]
        for pointer, command in cases:
            for kind in ('fifo', 'directory'):
                with self.subTest(pointer=pointer, kind=kind), self.replace(pointer, kind):
                    before = state(self.directory)
                    self.assertIn('regular file', self.cli(*command, code=1)['error'])
                    self.assertEqual(state(self.directory), before)
            self.cli(*command)
            with self.replace(pointer, 'symlink'):
                saved = self.directory / 'saved-entry'
                previous = saved.read_bytes()
                self.cli(*command)
                self.assertFalse(pointer.is_symlink())
                self.assertEqual(saved.read_bytes(), previous)

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

    def test_reserved_candidate_names_protect_incomplete_case_aliases(self):
        snapshot = self.refresh()
        for prefix in ('snapshot', 'export', 'release'):
            candidate = self.directory / (prefix + '-v1-' + 'a' * 64)
            candidate.mkdir()
            (candidate / 'orrery.sqlite3').write_text('retained incomplete candidate evidence')
            alias = candidate.with_name(candidate.name.upper())
            before = state(self.directory)
            commands = [('build-db', '--store', self.store, '--database', alias / 'orrery.sqlite3'),
                        ('export', '--store', self.store, '--output', alias),
                        ('prepare-release', '--store', self.store, '--snapshot', snapshot['snapshot_version'],
                         '--output', alias, '--producer-commit', test_releases.COMMIT),
                        ('refresh', '--store', alias, *self.local_args())]
            for command in commands:
                with self.subTest(prefix=prefix, command=command[0]):
                    self.cli(*command, code=1)
                    self.assertEqual(state(self.directory), before)

    def test_workflow_url_preflight_preserves_baseline_before_build(self):
        env = {key: value for key, value in os.environ.items()
               if key not in ('GITHUB_OUTPUT', 'GITHUB_STEP_SUMMARY')}
        env.update(RELEASE_PRODUCER_COMMIT=test_releases.COMMIT,
                   RELEASE_BASELINE_COUNTS=json.dumps(dict.fromkeys(
                       ('orbital_records', 'discovery_records', 'master_records', 'known_discovery'), 0)),
                   RELEASE_SELECTED_LIMITS='2', RELEASE_ALLOW_COUNT_DECREASE='false')
        work = self.directory / 'workflow'
        for existing in (False, True):
            if existing:
                work.mkdir()
                (work / 'baseline-counts.json').write_text('previous baseline')
            for name in ('mpcorb', 'numbered'):
                before = state(self.directory)
                result = subprocess.run([sys.executable, '-B', str(test_releases.ROOT / 'scripts/prepare_release_workflow.py'),
                                         '--work-dir', str(work), '--' + name + '-url', 'http://example.test:99999'],
                                        cwd=test_releases.ROOT, env=env, capture_output=True, text=True, timeout=5)
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                self.assertIn('HTTP(S) URL', result.stderr)
                self.assertFalse(result.stdout)
                self.assertEqual(state(self.directory), before)
