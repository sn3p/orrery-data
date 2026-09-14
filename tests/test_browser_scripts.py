"""Run the manual entry points from a clean copied checkout and another cwd."""

from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest

ROOT = Path(__file__).resolve().parents[1]


class BrowserScripts(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.checkout = self.root / 'checkout'
        self.checkout.mkdir()
        for name in ('orrery_data', 'scripts'):
            shutil.copytree(ROOT / name, self.checkout / name, ignore=shutil.ignore_patterns('__pycache__'))
        for name in ('MPCORB.DAT', 'NumberedMPs.txt'):
            shutil.copyfile(ROOT / 'tests/fixtures' / name, self.root / name)

    def script(self, name, *args, code=0):
        result = subprocess.run([sys.executable, self.checkout / 'scripts' / name, *map(str, args)],
                                cwd=self.root, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, code, result.stdout + result.stderr)
        return json.loads(result.stdout) if code == 0 else result.stderr

    def update(self):
        return self.script('update_browser.py', '--mpcorb', 'MPCORB.DAT', '--numbered', 'NumberedMPs.txt')

    def state(self):
        return {str(p.relative_to(self.checkout / 'data')): (p.read_bytes(), p.stat().st_mtime_ns)
                for p in (self.checkout / 'data').rglob('*') if p.is_file()}

    def test_clean_manual_acquisition_projection_noop_and_offline_bundle(self):
        result = self.update()
        self.assertGreater(result['records'], 0)
        before = self.state()
        self.assertEqual(self.update()['status'], 'unchanged')
        self.assertEqual(before, self.state())
        bundle = next((self.checkout / 'artifacts/indexed').glob('delivery-v1-*'))
        result = self.script('update_browser.py', '--from-bundle', bundle.relative_to(self.root))
        self.assertEqual(result['status'], 'unchanged')
        self.assertEqual(before, self.state())
        self.assertFalse((self.root / '.data').exists())
        self.assertFalse((self.root / 'artifacts').exists())

    def test_invalid_arguments_and_output_rejected_before_acquisition(self):
        self.script('update_browser.py', '--mpcorb', 'MPCORB.DAT', code=2)
        self.script('update_browser.py', '--from-bundle', 'bundle', '--numbered', 'NumberedMPs.txt', code=2)
        for path in ('orrery_data', '.data', 'artifacts', '.'):
            self.script('update_browser.py', '--output', self.checkout / path, code=1)
        self.assertFalse((self.checkout / '.data').exists())
        self.assertFalse((self.checkout / 'artifacts').exists())

    def test_pages_staging_skip_force_and_validation(self):
        self.update()
        staged = self.script('prepare_browser_site.py', '--output', 'site')
        self.assertEqual(staged['status'], 'prepared')
        self.assertTrue((self.root / 'site/.nojekyll').is_file())
        for name, (body, _) in self.state().items():
            self.assertEqual((self.root / 'site' / name).read_bytes(), body)

        class QuietHandler(SimpleHTTPRequestHandler):
            def log_message(self, *args): pass
        server = ThreadingHTTPServer(('127.0.0.1', 0), partial(QuietHandler, directory=self.root / 'site'))
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        url = f'http://127.0.0.1:{server.server_port}/latest.json'
        skipped = self.script('prepare_browser_site.py', '--output', 'skipped', '--latest-url', url)
        self.assertEqual(skipped['status'], 'unchanged')
        self.assertFalse((self.root / 'skipped').exists())
        forced = self.script('prepare_browser_site.py', '--output', 'forced', '--latest-url', url, '--force')
        self.assertEqual(forced['status'], 'prepared')
        prepared = self.script('prepare_browser_site.py', '--output', 'fallback', '--latest-url', url + '-missing')
        self.assertEqual(prepared['status'], 'prepared')
        self.script('prepare_browser_site.py', '--output', 'site', code=2)
        self.script('prepare_browser_site.py', '--output', self.checkout / 'data/nested', code=2)
        chunk = next((self.checkout / 'data/chunks').glob('*.json'))
        chunk.write_text('[]')
        self.script('prepare_browser_site.py', '--output', 'invalid', code=1)
        self.assertFalse((self.root / 'invalid').exists())
