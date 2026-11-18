"""Complete-site publication and independent product ownership, without servers."""

import importlib.util
import io
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from orrery_data.browser import sync_candidate, verify_browser
from orrery_data.site import verify_site
from orrery_data.formats import DataError

ROOT = Path(__file__).resolve().parents[1]


class CometPublication(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.output = self.root / 'data/comets'
        shutil.copytree(ROOT / 'data/comets', self.output)

    def test_orphan_comets_do_not_block_asteroid_update_but_block_publication(self):
        orphan = self.output / 'objects' / ('0' * 64 + '.json')
        orphan.write_text('{}')
        before = {p.relative_to(self.output): p.read_bytes() for p in self.output.rglob('*') if p.is_file()}
        # Exercise the real orchestration CLI, exporter and post-install verifier.
        result = subprocess.run([sys.executable, str(ROOT / 'scripts/update_browser.py'),
                                 '--from-bundle', str(ROOT / 'tests/fixtures/consumer-v1/ties'),
                                 '--output', str(self.output.parent)], cwd=self.root, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertGreater(json.loads(result.stdout)['records'], 0)
        verify_browser(self.output.parent)
        self.assertEqual(before, {p.relative_to(self.output): p.read_bytes() for p in self.output.rglob('*') if p.is_file()})
        with self.assertRaises(DataError): verify_site(self.output.parent)
        for name in ('orrery_data', 'scripts'):
            shutil.copytree(ROOT / name, self.root / name, ignore=shutil.ignore_patterns('__pycache__'))
        staged = self.root / 'pages'
        failed = subprocess.run([sys.executable, str(self.root / 'scripts/prepare_browser_site.py'),
                                 '--output', str(staged)], cwd=self.root, capture_output=True, text=True)
        self.assertNotEqual(failed.returncode, 0)
        self.assertIn('Comet inventory differs', failed.stderr)
        self.assertFalse(staged.exists())
        # Site verification visits the comet product once, independently of asteroid files.
        orphan.unlink()
        with patch('orrery_data.site.verify_comets', wraps=__import__('orrery_data.comets', fromlist=['verify_comets']).verify_comets) as verify:
            verify_site(self.output.parent)
            verify.assert_called_once_with(self.output)
        shutil.rmtree(self.output)
        self.output.symlink_to(ROOT / 'data/comets', target_is_directory=True)
        with self.assertRaises(DataError): verify_browser(self.output.parent)

    def test_asteroid_export_preserves_comets_and_site_reconciles_both(self):
        data = self.output.parent
        fixture = ROOT / 'tests/fixtures/browser-v1/ties'
        for path in fixture.iterdir():
            if path.is_dir(): shutil.copytree(path, data / path.name)
            else: shutil.copyfile(path, data / path.name)
        comet_before = {p.relative_to(self.output): p.read_bytes() for p in self.output.rglob('*') if p.is_file()}
        # An interrupted comet candidate remains outside the public site.
        abandoned = self.root / '.comets-interrupted'; abandoned.mkdir(); (abandoned / 'partial').write_text('x')
        stage = self.root / 'asteroids'; shutil.copytree(fixture, stage)
        sync_candidate(stage, data)
        self.assertEqual(comet_before, {p.relative_to(self.output): p.read_bytes() for p in self.output.rglob('*') if p.is_file()})
        verify_browser(data)
        spec = importlib.util.spec_from_file_location('prepare_site', ROOT / 'scripts/prepare_browser_site.py')
        site = importlib.util.module_from_spec(spec); spec.loader.exec_module(site)
        remote = {'https://example.test/sub/latest.json': (data / 'latest.json').read_bytes(),
                  'https://example.test/sub/comets/latest.json': (self.output / 'latest.json').read_bytes()}
        def open_remote(url, timeout):
            if url not in remote: raise OSError('missing remote product')
            return io.BytesIO(remote[url])
        def invoke(name):
            out = self.root / name
            with patch.object(site, 'ROOT', self.root), patch.object(site, 'urlopen', side_effect=open_remote), \
                 patch.object(site.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, json.dumps(verify_browser(data)), '')), \
                 patch.object(sys, 'argv', ['prepare', '--output', str(out), '--latest-url', 'https://example.test/sub/latest.json']), \
                 patch('sys.stdout', new_callable=io.StringIO) as stdout:
                self.assertEqual(site.main(), 0)
                return json.loads(stdout.getvalue()), out
        self.assertEqual(invoke('same')[0]['status'], 'unchanged')
        remote['https://example.test/sub/comets/latest.json'] = b'{}'
        result, out = invoke('comet-stale'); self.assertEqual(result['status'], 'prepared')
        self.assertEqual((out / 'latest.json').read_bytes(), (data / 'latest.json').read_bytes())
        self.assertEqual((out / 'comets/latest.json').read_bytes(), (self.output / 'latest.json').read_bytes())
        del remote['https://example.test/sub/comets/latest.json']
        self.assertEqual(invoke('missing-comet')[0]['status'], 'prepared')
        remote['https://example.test/sub/latest.json'] = b'{}'
        self.assertEqual(invoke('both-stale')[0]['status'], 'prepared')
        # Deliberate product removal is also a complete-site change.
        remote['https://example.test/sub/latest.json'] = (data / 'latest.json').read_bytes()
        remote['https://example.test/sub/comets/latest.json'] = (self.output / 'latest.json').read_bytes()
        shutil.rmtree(self.output)
        result, out = invoke('removed-local')
        self.assertEqual(result['status'], 'prepared')
        self.assertFalse((out / 'comets').exists())



if __name__ == '__main__': unittest.main()
