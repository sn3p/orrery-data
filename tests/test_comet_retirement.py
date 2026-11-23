"""Retired v1 cannot enter a new site; stale hosted v1 triggers replacement."""
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
from urllib.error import HTTPError
from orrery_data.formats import DataError
from orrery_data.site import verify_site

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('retirement_site', ROOT/'scripts/prepare_browser_site.py')
site = importlib.util.module_from_spec(spec)
spec.loader.exec_module(site)

class Retirement(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        for name in ('orrery_data', 'scripts'):
            shutil.copytree(ROOT/name, self.root/name, ignore=shutil.ignore_patterns('__pycache__'))
        self.data = self.root/'data'
        shutil.copytree(ROOT/'tests/fixtures/browser-v1/ties', self.data)
        shutil.copytree(ROOT/'data/comets-v2', self.data/'comets-v2')

    def test_real_staging_excludes_v1_and_preserves_every_supported_byte(self):
        output = self.root/'pages'
        run = subprocess.run([sys.executable, str(self.root/'scripts/prepare_browser_site.py'),
                              '--output', str(output)], cwd=self.root, capture_output=True, text=True)
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(json.loads(run.stdout)['comets_v2']['objects'], 6)
        expected = {str(p.relative_to(self.data)): p.read_bytes() for p in self.data.rglob('*') if p.is_file()}
        actual = {str(p.relative_to(output)): p.read_bytes() for p in output.rglob('*') if p.is_file()}
        self.assertEqual(actual, {**expected, '.nojekyll': b''})
        self.assertFalse((output/'comets').exists())

    def test_leftover_v1_namespace_blocks_real_publication(self):
        legacy = self.data/'comets'; legacy.mkdir(); (legacy/'latest.json').write_text('{}')
        with self.assertRaises(DataError): verify_site(self.data)
        output = self.root/'pages'
        run = subprocess.run([sys.executable, str(self.root/'scripts/prepare_browser_site.py'),
                              '--output', str(output)], capture_output=True, text=True)
        self.assertNotEqual(run.returncode, 0)
        self.assertFalse(output.exists())

    def test_stale_v1_prepares_replacement_even_with_unchanged_active_pins(self):
        base = 'https://example.test/sub/'
        for status, body, expected in [(200,b'{"comet_contract_version":1}',False),
                                       (200,b'null',False),(404,b'',True),(500,b'',False)]:
            def opening(url, timeout):
                if url == base+'comets/latest.json':
                    if status != 200: raise HTTPError(url,status,'test',{},None)
                    return io.BytesIO(body)
                return io.BytesIO((self.data/url.removeprefix(base)).read_bytes())
            with self.subTest(status=status,body=body), patch.object(site,'urlopen',side_effect=opening):
                self.assertEqual(site.published_site_matches(self.data,base+'latest.json'),expected)
                output = self.root/f'pages-{status}-{len(body)}'
                # Real verifier subprocess and actual complete artifact copy; only network is intercepted.
                with patch.object(site,'ROOT',self.root), patch.object(sys,'argv',[
                    'prepare','--output',str(output),'--latest-url',base+'latest.json']), \
                     patch('sys.stdout',new_callable=io.StringIO) as stdout:
                    self.assertEqual(site.main(),0)
                self.assertEqual(json.loads(stdout.getvalue())['status'], 'unchanged' if expected else 'prepared')
                self.assertEqual(output.exists(),not expected)
                self.assertFalse((output/'comets').exists())

    def test_retired_commands_fail_without_creating_output(self):
        for command in ('refresh-comets','verify-comets'):
            run=subprocess.run([sys.executable,'-m','orrery_data',command],cwd=self.root,capture_output=True,text=True)
            self.assertEqual(run.returncode,2,run.stderr)
            self.assertIn('invalid choice',run.stderr)
        self.assertFalse((self.data/'comets').exists())
