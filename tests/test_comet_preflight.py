"""Rejected comet layouts must not mutate the site, cache or lock namespace."""

from contextlib import chdir
import hashlib
import io
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from orrery_data import comets
from orrery_data.cli import main
from orrery_data.site import verify_site

ROOT = Path(__file__).resolve().parents[1]


class CometPreflight(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.site = self.root / 'data'
        self.output = self.site / 'comets'
        shutil.copytree(ROOT / 'tests/fixtures/browser-v1/ties', self.site)
        shutil.copytree(ROOT / 'data/comets', self.output)

    def snapshot(self):
        result = {}
        for path in self.root.rglob('*'):
            if path.is_symlink(): value = ('symlink', os.readlink(path))
            elif path.is_dir(): value = ('directory',)
            else: value = ('file', hashlib.sha256(path.read_bytes()).hexdigest())
            result[path.relative_to(self.root).as_posix()] = value
        return result

    def reject_cli(self, cache, expected):
        before = self.snapshot()
        with patch.object(comets, 'acquire') as acquire, patch('sys.stderr', new_callable=io.StringIO) as stderr:
            self.assertEqual(main(['refresh-comets', '--cache', str(cache), '--output', str(self.output), '--offline']), 1)
            self.assertIn(expected, stderr.getvalue())
            acquire.assert_not_called()
        self.assertEqual(self.snapshot(), before)
        verify_site(self.site)

    def test_cache_overlap_with_any_site_namespace_rejects_before_writes(self):
        alias = self.root / 'site-alias'
        alias.symlink_to(self.site, target_is_directory=True)
        for cache in (self.site, self.site / 'chunks', self.site / 'new-cache', self.output,
                      self.root, alias / 'chunks', self.root / 'DATA/chunks'):
            with self.subTest(cache=cache):
                self.reject_cli(cache, 'must not overlap')
        # Invocation-relative paths pass through the same public CLI preflight.
        with chdir(self.root):
            self.reject_cli(Path('data/chunks'), 'must not overlap')

    def test_cross_filesystem_site_and_nested_mounts_reject_before_acquisition(self):
        original_stat = Path.stat
        object_file = next((self.output / 'objects').iterdir())
        for mount in (self.site, self.output, self.output / 'objects', object_file):
            def mounted_stat(path, *args, **kwargs):
                info = original_stat(path, *args, **kwargs)
                if path == mount or mount in path.parents:
                    values = list(info)
                    values[2] += 1  # st_dev; emulate a distinct mounted volume.
                    return os.stat_result(values)
                return info
            with self.subTest(mount=mount), patch.object(Path, 'stat', mounted_stat):
                self.reject_cli(self.root / 'cache', 'same filesystem')

    def test_new_same_filesystem_output_can_reach_acquisition_without_site_pollution(self):
        new_output = self.site / 'new-comets'
        before = self.snapshot()
        with patch.object(comets, 'acquire', side_effect=ValueError('acquisition sentinel')) as acquire, \
             patch('sys.stderr', new_callable=io.StringIO) as stderr:
            self.assertEqual(main(['refresh-comets', '--cache', str(self.root / 'cache'),
                                   '--output', str(new_output), '--offline']), 1)
            self.assertIn('acquisition sentinel', stderr.getvalue())
            acquire.assert_called_once()
        # Only the dedicated external refresh lock may be created, not public files.
        after = self.snapshot()
        self.assertEqual({k: v for k, v in after.items() if not k.startswith('.data-new-comets-refresh-lock')}, before)
        self.assertFalse(new_output.exists())
        self.assertFalse((self.root / 'cache').exists())
        verify_site(self.site)


if __name__ == '__main__': unittest.main()
