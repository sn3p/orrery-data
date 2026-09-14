"""Conditional requests through the real refresh CLI, including cache recovery."""

import gzip
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest

ROOT = Path(__file__).resolve().parents[1]


class SourceCacheCLI(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.store = self.root / 'store'
        self.inputs = {
            '/orbits': gzip.compress((ROOT / 'tests/fixtures/MPCORB.DAT').read_bytes()),
            '/dates': (ROOT / 'tests/fixtures/NumberedMPs.txt').read_bytes()}
        self.etags = dict.fromkeys(self.inputs, '"initial"')
        self.calls, self.fail, self.redirects = [], False, {}
        owner = self
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path in owner.redirects:
                    self.send_response(302)
                    self.send_header('Location', owner.redirects[self.path])
                    self.end_headers()
                    return
                etag = owner.etags[self.path]
                conditional = self.headers.get('If-None-Match')
                status = 503 if owner.fail else 304 if conditional == etag else 200
                owner.calls.append((self.path, conditional, status))
                self.send_response(status)
                self.send_header('ETag', etag)
                if status == 200:
                    self.send_header('Content-Length', str(len(owner.inputs[self.path])))
                self.end_headers()
                if status == 200:
                    self.wfile.write(owner.inputs[self.path])
            def log_message(self, *args): pass
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.url = f'http://127.0.0.1:{self.server.server_port}'

    def refresh(self, *extra, code=0, date_path='/dates'):
        args = [sys.executable, '-m', 'orrery_data', 'refresh', '--store', str(self.store),
                '--reuse-unchanged', '--mpcorb-url', self.url + '/orbits', '--numbered-url', self.url + date_path, *map(str, extra)]
        result = subprocess.run(args, cwd=ROOT, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, code, result.stdout + result.stderr)
        return json.loads(result.stdout if code == 0 else result.stderr)

    def test_304_reuses_verified_inputs_and_preserves_provenance(self):
        first = self.refresh()
        manifest = Path(first['path']) / 'snapshot.json'
        before = manifest.read_bytes(), manifest.stat().st_mtime_ns
        self.calls.clear()
        result = self.refresh()
        self.assertEqual(result['status'], 'unchanged')
        self.assertEqual(before, (manifest.read_bytes(), manifest.stat().st_mtime_ns))
        self.assertEqual(self.calls, [('/orbits', '"initial"', 304), ('/dates', '"initial"', 304)])

    def test_one_changed_source_full_reconciliation_and_network_failure(self):
        first = self.refresh()
        self.inputs['/dates'] = self.inputs['/dates'].replace(b'1801 01 01', b'1800 01 01')
        self.etags['/dates'] = '"changed"'
        self.calls.clear()
        second = self.refresh()
        self.assertNotEqual(first['snapshot_version'], second['snapshot_version'])
        self.assertEqual([call[2] for call in self.calls], [304, 200])
        manifest = json.loads((Path(second['path']) / 'snapshot.json').read_bytes())
        self.assertEqual(manifest['sources']['numbered']['etag'], '"changed"')
        before = (self.store / 'current.json').read_bytes()
        self.fail = True
        self.refresh(code=1)
        self.assertEqual(before, (self.store / 'current.json').read_bytes())

    def test_missing_corrupt_cache_and_changed_url_use_full_get(self):
        first = self.refresh()
        cache = self.store / 'http-cache'
        for corrupt in ('missing', 'body', 'metadata', 'content_length'):
            with self.subTest(corrupt=corrupt):
                if corrupt == 'missing': (cache / 'mpcorb.input').unlink()
                if corrupt == 'body': (cache / 'mpcorb.input').write_bytes(b'bad')
                if corrupt == 'metadata': (cache / 'mpcorb.json').write_text('{}')
                if corrupt == 'content_length':
                    metadata = json.loads((cache / 'mpcorb.json').read_text())
                    metadata['content_length'] = str(metadata['bytes'] + 1)
                    (cache / 'mpcorb.json').write_text(json.dumps(metadata))
                self.calls.clear()
                self.assertEqual(self.refresh()['snapshot_version'], first['snapshot_version'])
                self.assertEqual(self.calls[0], ('/orbits', None, 200))
        self.inputs['/new-dates'] = self.inputs['/dates']
        self.etags['/new-dates'] = '"initial"'
        self.calls.clear()
        self.refresh(date_path='/new-dates')
        self.assertEqual(self.calls[-1], ('/new-dates', None, 200))

    def test_changed_redirect_destination_retries_without_old_resource_etag(self):
        self.redirects['/latest-dates'] = '/dates'
        first = self.refresh(date_path='/latest-dates')
        self.calls.clear()
        self.assertEqual(self.refresh(date_path='/latest-dates')['status'], 'unchanged')
        self.assertEqual(self.calls[-1], ('/dates', '"initial"', 304))
        self.inputs['/new-dates'] = self.inputs['/dates'].replace(b'1801 01 01', b'1800 01 01')
        self.etags['/new-dates'] = '"initial"'
        self.redirects['/latest-dates'] = '/new-dates'
        self.calls.clear()
        second = self.refresh(date_path='/latest-dates')
        self.assertNotEqual(first['snapshot_version'], second['snapshot_version'])
        self.assertEqual(self.calls[-2:], [('/new-dates', '"initial"', 304), ('/new-dates', None, 200)])
        manifest = json.loads((Path(second['path']) / 'snapshot.json').read_bytes())
        self.assertEqual(manifest['sources']['numbered']['resolved_url'], self.url + '/new-dates')
        self.calls.clear()
        self.assertEqual(self.refresh(date_path='/latest-dates')['status'], 'unchanged')
        self.assertEqual(self.calls[-1], ('/new-dates', '"initial"', 304))

    def test_weak_validators_and_expected_hashes(self):
        self.etags['/orbits'] = 'W/"weak"'
        first = self.refresh()
        self.calls.clear()
        self.refresh()
        self.assertEqual(self.calls[0], ('/orbits', None, 200))
        metadata = self.root / 'expected.json'
        metadata.write_text(json.dumps({'numbered': {'sha256': '0' * 64}}))
        self.refresh('--source-metadata', metadata, code=1)
        self.assertEqual(json.loads((self.store / 'current.json').read_bytes())['snapshot_version'], first['snapshot_version'])

    def test_malformed_cached_etags_fall_back_and_repair_without_changing_provenance(self):
        first = self.refresh()
        manifest = Path(first['path']) / 'snapshot.json'
        before = manifest.read_bytes(), manifest.stat().st_mtime_ns
        metadata_path = self.store / 'http-cache/mpcorb.json'
        valid = json.loads(metadata_path.read_text())
        for etag in ('"bad\nvalue"', '"bad\rvalue"', '"bad\tvalue"', '"bad\x00value"',
                     '"bad\x7fvalue"', '"bad value"', '"bad"value"', '"bad\u0100value"'):
            with self.subTest(etag=repr(etag)):
                metadata_path.write_text(json.dumps({**valid, 'etag': etag}))
                self.calls.clear()
                result = self.refresh()
                self.assertEqual(result['status'], 'unchanged')
                self.assertEqual(result['snapshot_version'], first['snapshot_version'])
                self.assertEqual(self.calls[0], ('/orbits', None, 200))
                self.assertEqual(before, (manifest.read_bytes(), manifest.stat().st_mtime_ns))
                self.assertEqual(json.loads(metadata_path.read_text())['etag'], valid['etag'])
                self.calls.clear()
                self.refresh()
                self.assertEqual(self.calls[0], ('/orbits', valid['etag'], 304))

    def test_valid_empty_and_latin1_etags_remain_usable(self):
        for etag in ('""', '"!#~\x80\xff"'):
            with self.subTest(etag=repr(etag)):
                self.etags['/orbits'] = etag
                self.refresh()
                self.calls.clear()
                self.assertEqual(self.refresh()['status'], 'unchanged')
                self.assertEqual(self.calls[0], ('/orbits', etag, 304))
