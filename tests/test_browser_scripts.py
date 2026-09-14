"""Run update/publication entry points from a clean copied checkout and another cwd."""

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
        for name in ('orrery_data', 'scripts', '.github'):
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

    def git(self, *args):
        result = subprocess.run(['git', '-c', 'user.name=Test', '-c', 'user.email=test@example.invalid',
                                 '-c', 'commit.gpgsign=false', '-c', 'core.hooksPath=/dev/null', *args],
                                cwd=self.checkout, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout.strip()

    def commit(self):
        self.git('add', '--all')
        self.git('commit', '--quiet', '-m', 'Fixture change')
        return self.git('rev-parse', 'HEAD')

    def publication_checkout(self):
        self.update()
        self.git('init', '--quiet')
        (self.checkout / '.gitignore').write_text('__pycache__/\n.data/\nartifacts/\n')
        return self.commit()

    def serve(self, directory):
        class QuietHandler(SimpleHTTPRequestHandler):
            def log_message(self, *args): pass
        server = ThreadingHTTPServer(('127.0.0.1', 0), partial(QuietHandler, directory=directory))
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return f'http://127.0.0.1:{server.server_port}/latest.json'

    def assert_complete_site(self, directory):
        actual = {p.relative_to(directory).as_posix(): p.read_bytes()
                  for p in directory.rglob('*') if p.is_file()}
        self.assertEqual(actual, {**{name: body for name, (body, _) in self.state().items()}, '.nojekyll': b''})

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

        url = self.serve(self.root / 'site')
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

    def test_publication_code_push_redeploys_equal_data_across_complete_commit_range(self):
        self.publication_checkout()
        before_data = self.state()
        url = self.serve(self.checkout / 'data')
        for index, name in enumerate(('.github/workflows/browser-pages.yml', 'scripts/prepare_browser_site.py',
                                      'orrery_data/browser.py', 'orrery_data/contracts.py', 'orrery_data/cli.py')):
            with self.subTest(path=name):
                before = self.git('rev-parse', 'HEAD')
                path = self.checkout / name
                path.write_text(path.read_text() + '\n# Publication change\n')
                self.commit()
                # The final commit is unrelated; comparing HEAD^ would miss the publication change.
                (self.checkout / 'README.md').write_text(f'Documentation {index}\n')
                self.commit()
                result = self.script('prepare_browser_site.py', '--output', f'site-{index}',
                                     '--latest-url', url, '--publication-base', before)
                self.assertEqual(result['status'], 'prepared')
                self.assert_complete_site(self.root / f'site-{index}')
                self.assertEqual(self.state(), before_data)
                self.assertEqual(self.git('status', '--porcelain'), '')

    def test_data_only_push_skips_equal_descriptor_and_publishes_changed_dataset(self):
        before = self.publication_checkout()
        self.script('prepare_browser_site.py', '--output', 'published')
        url = self.serve(self.root / 'published')
        # A byte-only descriptor change is still the same catalogue.
        latest = self.checkout / 'data/latest.json'
        latest.write_text(latest.read_text() + '\n')
        self.commit()
        result = self.script('prepare_browser_site.py', '--output', 'equal', '--latest-url', url,
                             '--publication-base', before)
        self.assertEqual(result['status'], 'unchanged')
        self.assertFalse((self.root / 'equal').exists())
        before = self.git('rev-parse', 'HEAD')
        numbered = self.root / 'NumberedMPs.txt'
        numbered.write_text(numbered.read_text().replace('1801 01 01', '1801 01 02'))
        self.update()
        self.commit()
        before_data = self.state()
        result = self.script('prepare_browser_site.py', '--output', 'changed', '--latest-url', url,
                             '--publication-base', before)
        self.assertEqual(result['status'], 'prepared')
        self.assert_complete_site(self.root / 'changed')
        self.assertEqual(self.state(), before_data)
        self.assertEqual(self.git('status', '--porcelain'), '')

    def test_missing_publication_base_redeploys_but_never_bypasses_validation(self):
        self.publication_checkout()
        url = self.serve(self.checkout / 'data')
        for index, before in enumerate(('0' * 40, '1' * 40)):
            result = self.script('prepare_browser_site.py', '--output', f'fallback-{index}',
                                 '--latest-url', url, '--publication-base', before)
            self.assertEqual(result['status'], 'prepared')
            self.assert_complete_site(self.root / f'fallback-{index}')
        before = self.git('rev-parse', 'HEAD')
        path = self.checkout / 'scripts/prepare_browser_site.py'
        path.write_text(path.read_text() + '\n# Publication change\n')
        self.commit()
        chunk = next((self.checkout / 'data/chunks').glob('*.json'))
        valid = chunk.read_bytes()
        chunk.write_text('[]')
        self.script('prepare_browser_site.py', '--output', 'bad-hash', '--latest-url', url,
                    '--publication-base', before, '--force', code=1)
        self.assertFalse((self.root / 'bad-hash').exists())
        chunk.write_bytes(valid)
        (self.checkout / 'data/unexpected.txt').write_text('Unmanaged data')
        self.script('prepare_browser_site.py', '--output', 'bad-inventory', '--latest-url', url,
                    '--publication-base', before, code=1)
        self.assertFalse((self.root / 'bad-inventory').exists())
