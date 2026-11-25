"""Regressions at copying, verification and final installation boundaries."""
import shutil
from pathlib import Path
from unittest.mock import patch

import unittest
import test_comet_rollout as baseline
from test_comet_rollout import inventory, ROOT
from orrery_data import comet_rollout as rollout
from orrery_data import site as sites
from orrery_data.storage import rename_new, writer_lock, site_lock_path
from orrery_data.formats import DataError


class RolloutReview(unittest.TestCase):
    setUp = baseline.CometRollout.setUp
    prepare = baseline.CometRollout.prepare
    def test_candidate_swap_to_schema2_during_copy_is_rejected(self):
        copy = shutil.copytree
        def swapping(src, dst, *args, **kwargs):
            if Path(src) == self.candidate:
                src = self.site / 'comets'
            return copy(src, dst, *args, **kwargs)
        with patch.object(rollout.shutil, 'copytree', side_effect=swapping):
            with self.assertRaisesRegex(DataError, 'approved release'): self.prepare()
        self.assertFalse(self.output.exists())

    def test_valid_asteroid_swap_during_snapshot_is_rejected(self):
        copy = shutil.copytree
        def swapping(src, dst, *args, **kwargs):
            if Path(src) == self.site:
                for p in self.site.iterdir():
                    if p.name == 'comets': continue
                    if p.is_dir(): shutil.rmtree(p)
                    else: p.unlink()
                for p in (ROOT/'tests/fixtures/browser-v1/empty').iterdir():
                    if p.is_dir(): copy(p, self.site/p.name)
                    else: shutil.copyfile(p, self.site/p.name)
            return copy(src, dst, *args, **kwargs)
        with patch.object(sites.shutil, 'copytree', side_effect=swapping):
            with self.assertRaisesRegex(DataError, 'descriptors changed'): self.prepare()
        self.assertFalse(self.output.exists())

    def test_chunk_corruption_after_snapshot_is_rejected(self):
        copy = sites.copy_site_snapshot
        def corrupt(src, dst):
            copy(src, dst)
            next((dst/'chunks').iterdir()).write_bytes(b'{}')
        with patch.object(sites, 'copy_site_snapshot', side_effect=corrupt):
            with self.assertRaises(DataError): self.prepare()
        self.assertFalse(self.output.exists())

    def test_final_install_preserves_competing_empty_directory(self):
        created = []
        def race(src, dst):
            dst.mkdir(); created.append(dst.stat().st_ino)
            return rename_new(src, dst)
        with patch.object(rollout, 'rename_new', side_effect=race):
            with self.assertRaises(FileExistsError): self.prepare()
        self.assertEqual(self.output.stat().st_ino, created[0])
        self.assertEqual(list(self.output.iterdir()), [])

    def test_full_validation_occurs_outside_source_lock_and_old_comets_copy_once(self):
        verify = sites.verify_site
        calls = []
        def verifying(directory):
            with writer_lock(site_lock_path(self.site)): pass
            calls.append(Path(directory).name)
            return verify(directory)
        copy = shutil.copytree
        old_copies = []
        def copying(src, dst, *args, **kwargs):
            if Path(src) == self.site / 'comets': old_copies.append(str(dst))
            return copy(src, dst, *args, **kwargs)
        with patch.object(sites, 'verify_site', side_effect=verifying), \
             patch.object(rollout.shutil, 'copytree', side_effect=copying):
            self.prepare()
        self.assertEqual(calls, ['rollback', 'site'])
        self.assertEqual(len(old_copies), 1)

    def test_schema3_dispatch_reads_descriptor_and_inventory_once(self):
        with patch.object(rollout, 'managed_files', wraps=rollout.managed_files) as files, \
             patch.object(rollout, 'read_json', wraps=rollout.read_json) as reads:
            rollout.verify_current_comets(self.candidate)
        files.assert_called_once_with(self.candidate)
        reads.assert_called_once_with(self.candidate/'latest.json')

    def test_explicit_pages_source_does_not_mutate_release_bundle(self):
        self.prepare()
        before = inventory(self.output)
        entries = sorted(str(p.relative_to(self.output)) for p in self.output.rglob('*'))
        import subprocess, sys
        run = subprocess.run([sys.executable, str(ROOT/'scripts/prepare_browser_site.py'),
            '--source', str(self.output/'site'), '--output', str(self.root/'pages')], capture_output=True, text=True)
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(inventory(self.output), before)
        self.assertEqual(sorted(str(p.relative_to(self.output)) for p in self.output.rglob('*')), entries)
