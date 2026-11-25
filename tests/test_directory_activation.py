"""Exact v4 release through public CLI, staging, rollback and writer boundaries."""
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from orrery_data.comet_rollout import DIRECTORY_PIN, HISTORY_PIN, verify_current_comets, prepare_rollout
from orrery_data.formats import DataError
from orrery_data.comets import refresh_comets
from orrery_data.paths import validate_writable_path
from test_comet_rollout import history_fixture, inventory, ROOT


class DirectoryActivation(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.site = self.root / 'site'
        shutil.copytree(ROOT / 'tests/fixtures/browser-v1/ties', self.site)
        history_fixture(self.site / 'comets')
        self.candidate = self.root / 'candidate'
        shutil.copytree(ROOT / 'data/comets', self.candidate)

    def test_cli_staging_rollback_and_asteroid_preservation(self):
        before = inventory(self.site)
        release = self.root / 'release'
        run = subprocess.run([sys.executable, '-m', 'orrery_data', 'prepare-comet-rollout',
            '--site', str(self.site), '--candidate', str(self.candidate), '--output', str(release),
            '--expected-current-pin', HISTORY_PIN], capture_output=True, text=True)
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(json.loads(run.stdout)['pin']['sha256'], DIRECTORY_PIN)
        self.assertEqual(inventory(release / 'rollback'), before)
        self.assertEqual(inventory(self.site), before)
        for site in ('site', 'rollback'):
            run = subprocess.run([sys.executable, 'scripts/prepare_browser_site.py', '--source',
                str(release / site), '--output', str(self.root / ('pages-' + site))], capture_output=True, text=True)
            self.assertEqual(run.returncode, 0, run.stderr)
        report = verify_current_comets(release / 'site/comets')
        self.assertEqual((report['objects'], report['trajectories'], report['files']), (4089, 14, 27))
        self.assertEqual({k:v for k,v in inventory(release/'site').items() if not k.startswith('comets/')},
                         {k:v for k,v in before.items() if not k.startswith('comets/')})
        with self.assertRaisesRegex(DataError, 'pin changed'):
            prepare_rollout(self.site, self.candidate, self.root/'stale', '0'*64)
        self.assertFalse((self.root/'stale').exists())

    def test_corrupt_missing_extra_symlink_and_unapproved_fail_closed(self):
        original = inventory(self.candidate)
        for kind in ('pin', 'manifest', 'payload', 'legacy', 'missing', 'extra', 'symlink'):
            with self.subTest(kind=kind):
                shutil.rmtree(self.candidate)
                shutil.copytree(ROOT/'data/comets', self.candidate)
                manifest = next((self.candidate/'manifests').iterdir())
                payload = next((self.candidate/'objects').iterdir())
                if kind == 'pin':
                    latest = json.loads((self.candidate/'latest.json').read_bytes())
                    latest['index']['sha256'] = '0'*64
                    (self.candidate/'latest.json').write_text(json.dumps(latest))
                elif kind == 'manifest': manifest.write_bytes(b'{}')
                elif kind == 'payload': payload.write_bytes(b'{}')
                elif kind == 'legacy': (self.candidate/f'index-{HISTORY_PIN}.json').write_bytes(b'{}')
                elif kind == 'missing': manifest.unlink()
                elif kind == 'extra': (self.candidate/('index-'+'0'*64+'.json')).write_bytes(b'{}')
                else:
                    target=self.root/'outside'; target.write_bytes(manifest.read_bytes()); manifest.unlink(); manifest.symlink_to(target)
                with self.assertRaises((DataError, OSError)):
                    prepare_rollout(self.site, self.candidate, self.root/'invalid', HISTORY_PIN)
                self.assertFalse((self.root/'invalid').exists())

    def test_immutable_v4_writers_reject_before_acquisition(self):
        before = inventory(self.candidate)
        with patch('orrery_data.horizons.acquire') as acquire:
            with self.assertRaisesRegex(DataError, 'immutable schema-4'):
                refresh_comets(self.root/'cache', self.candidate)
            acquire.assert_not_called()
        alias = self.root/'alias'; alias.symlink_to(self.candidate, target_is_directory=True)
        for target in (self.candidate, alias, self.candidate/'manifests/new'):
            with self.assertRaisesRegex(DataError, 'immutable'):
                validate_writable_path(target)
        self.assertFalse((self.root/'cache').exists())
        self.assertEqual(inventory(self.candidate), before)
