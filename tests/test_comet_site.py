"""The site publishes only the current catalogue using the existing force policy."""
from contextlib import chdir
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
from orrery_data.formats import DataError
from orrery_data.site import verify_site

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('catalogue_site', ROOT/'scripts/prepare_browser_site.py')
site = importlib.util.module_from_spec(spec)
spec.loader.exec_module(site)

class SitePublication(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        for name in ('orrery_data', 'scripts'):
            shutil.copytree(ROOT/name, self.root/name, ignore=shutil.ignore_patterns('__pycache__'))
        self.data = self.root/'data'
        shutil.copytree(ROOT/'tests/fixtures/browser-v1/ties', self.data)
        shutil.copytree(ROOT/'data/comets', self.data/'comets')

    def test_real_staging_excludes_versioned_product_and_preserves_every_supported_byte(self):
        output = self.root/'pages'
        run = subprocess.run([sys.executable, str(self.root/'scripts/prepare_browser_site.py'),
                              '--output', str(output)], cwd=self.root, capture_output=True, text=True)
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(json.loads(run.stdout)['comets']['objects'], 4089)
        expected = {str(p.relative_to(self.data)): p.read_bytes() for p in self.data.rglob('*') if p.is_file()}
        actual = {str(p.relative_to(output)): p.read_bytes() for p in output.rglob('*') if p.is_file()}
        self.assertEqual(actual, {**expected, '.nojekyll': b''})
        self.assertFalse((output/'comets-v2').exists())

    def test_leftover_versioned_namespace_blocks_real_publication(self):
        legacy = self.data/'comets-v2'; legacy.mkdir(); (legacy/'latest.json').write_text('{}')
        with self.assertRaises(DataError): verify_site(self.data)
        output = self.root/'pages'
        run = subprocess.run([sys.executable, str(self.root/'scripts/prepare_browser_site.py'),
                              '--output', str(output)], capture_output=True, text=True)
        self.assertNotEqual(run.returncode, 0)
        self.assertFalse(output.exists())

    def test_equal_products_request_only_active_descriptors(self):
        base = 'https://example.test/sub/'
        requests = []
        def opening(url, timeout):
            requests.append(url)
            return io.BytesIO((self.data/url.removeprefix(base)).read_bytes())
        with patch.object(site,'urlopen',side_effect=opening):
            self.assertTrue(site.published_site_matches(self.data,base+'latest.json'))
        self.assertEqual(requests, [base+'latest.json',base+'comets/latest.json'])

    def test_cached_schema1_descriptor_triggers_complete_publication(self):
        base = 'https://example.test/sub/'
        current = json.loads((self.data/'comets/latest.json').read_text())
        stale = dict(current, comet_contract_version=1)
        requests = []
        def opening(url, timeout):
            requests.append(url)
            if url == base+'comets/latest.json':
                return io.BytesIO(json.dumps(stale).encode())
            return io.BytesIO((self.data/url.removeprefix(base)).read_bytes())
        output = self.root/'replacement'
        with patch.object(site, 'ROOT', self.root), patch.object(site, 'urlopen', side_effect=opening), \
             patch.object(sys, 'argv', ['prepare', '--output', str(output), '--latest-url', base+'latest.json']), \
             patch('sys.stdout', new_callable=io.StringIO) as stdout:
            self.assertEqual(site.main(), 0)
            self.assertEqual(json.loads(stdout.getvalue())['status'], 'prepared')
        self.assertEqual(requests, [base+'latest.json', base+'comets/latest.json'])
        self.assertEqual(json.loads((output/'comets/latest.json').read_text()), current)
        expected = {str(p.relative_to(self.data)): p.read_bytes() for p in self.data.rglob('*') if p.is_file()}
        actual = {str(p.relative_to(output)): p.read_bytes() for p in output.rglob('*') if p.is_file()}
        self.assertEqual(actual, {**expected, '.nojekyll': b''})

    def test_publication_code_change_and_explicit_retry_force_complete_replacement(self):
        def git(*args):
            return subprocess.check_output(['git','-c','user.name=Test','-c','user.email=test@example.invalid',
                '-c','commit.gpgsign=false','-c','core.hooksPath=/dev/null',*args],cwd=self.root,text=True).strip()
        git('init','--quiet')
        (self.root/'.gitignore').write_text('__pycache__/\n')
        git('add','scripts/prepare_browser_site.py');git('commit','--quiet','-m','before')
        before=git('rev-parse','HEAD')
        script=self.root/'scripts/prepare_browser_site.py'
        script.write_text(script.read_text()+'\n# Publication code change\n')
        git('add',str(script));git('commit','--quiet','-m','publication')
        # A later unrelated commit must not hide the publication change.
        (self.root/'README.md').write_text('notes')
        git('add','README.md');git('commit','--quiet','-m','notes')
        for name,args in [('merge',['--publication-base',before]),
                          ('rerun',['--publication-base',before]),('manual',['--force'])]:
            with self.subTest(name=name), chdir(self.root), patch.object(site,'ROOT',self.root), \
                 patch.object(site,'urlopen',side_effect=AssertionError('force must not consult cached hosting')), \
                 patch.object(sys,'argv',['prepare','--output',str(self.root/name),
                    '--latest-url','https://example.test/sub/latest.json',*args]), \
                 patch('sys.stdout',new_callable=io.StringIO) as stdout:
                self.assertEqual(site.main(),0)
                self.assertEqual(json.loads(stdout.getvalue())['status'],'prepared')
            self.assertFalse((self.root/name/'comets-v2').exists())
            self.assertTrue((self.root/name/'comets/latest.json').is_file())
        # Normal scheduled/manual reconciliation uses current products only.
        with chdir(self.root), patch.object(site,'ROOT',self.root), \
             patch.object(site,'urlopen',side_effect=lambda url,timeout: io.BytesIO(
                (self.data/url.removeprefix('https://example.test/sub/')).read_bytes())), \
             patch.object(sys,'argv',['prepare','--output',str(self.root/'unchanged'),
                 '--latest-url','https://example.test/sub/latest.json','--publication-base',git('rev-parse','HEAD')]), \
             patch('sys.stdout',new_callable=io.StringIO) as stdout:
            self.assertEqual(site.main(),0)
            self.assertEqual(json.loads(stdout.getvalue())['status'],'unchanged')

    def test_removed_versioned_commands_fail_without_creating_output(self):
        for command in ('refresh-comets-v2','verify-comets-v2'):
            run=subprocess.run([sys.executable,'-m','orrery_data',command],cwd=self.root,capture_output=True,text=True)
            self.assertEqual(run.returncode,2,run.stderr)
            self.assertIn('invalid choice',run.stderr)
        self.assertFalse((self.data/'comets-v2').exists())
