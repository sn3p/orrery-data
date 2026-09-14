"""Exercise browser generation at CLI, update and standalone verification boundaries."""

import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from orrery_data.browser import export_browser, verify_browser
from orrery_data.formats import DataError
from orrery_data.storage import writer_lock

ROOT = Path(__file__).resolve().parents[1]
FIX = ROOT / 'tests/fixtures'


class BrowserCLI(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.output = self.root / 'data'

    def cli(self, *args, code=0):
        result = subprocess.run([sys.executable, '-m', 'orrery_data', *map(str, args)],
                                cwd=ROOT, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, code, result.stdout + result.stderr)
        return json.loads(result.stdout if code == 0 else result.stderr)

    def state(self):
        return {str(p.relative_to(self.output)): (p.read_bytes(), p.stat().st_mtime_ns)
                for p in self.output.rglob('*') if p.is_file()}

    def project(self, bundle='ties'):
        return self.cli('export-browser', '--bundle', FIX / 'consumer-v1' / bundle, '--output', self.output)

    def rows(self):
        latest = json.loads((self.output / 'latest.json').read_bytes())
        index = json.loads((self.output / latest['index']['url']).read_bytes())
        return [row for chunk in index['chunks'] for row in json.loads((self.output / chunk['url']).read_bytes())]

    def test_round_trip_noop_and_migration_fixtures(self):
        result = self.project()
        self.assertEqual(result['records'], 6)
        self.assertEqual(self.rows(), json.loads((FIX / 'consumer-v1/ties/full/catalog.json').read_bytes()))
        before = self.state()
        again = self.project()
        self.assertEqual(again['status'], 'unchanged')
        self.assertEqual(before, self.state())
        self.assertEqual(set(again['changes']['unchanged']), set(before))
        self.cli('verify-browser', '--directory', self.output, '--index-sha256', result['pin']['sha256'])
        self.cli('verify-indexed', '--bundle', FIX / 'consumer-v1/ties')
        for name, (contents, _) in before.items():
            self.assertEqual(contents, (FIX / 'browser-v1/ties' / name).read_bytes())

    def test_empty_removes_obsolete_files_and_keeps_identical_notice(self):
        self.project()
        before = self.state()
        result = self.project('empty')
        self.assertEqual(result['records'], 0)
        self.assertEqual(self.rows(), [])
        self.assertTrue(result['changes']['deleted'])
        for name in result['changes']['unchanged']:
            self.assertEqual(before[name], self.state()[name])
        self.assertFalse((self.output / 'chunks').exists())
        self.cli('verify-browser', '--directory', self.output)

    def test_invalid_input_and_unrelated_output_do_not_replace_current(self):
        self.project()
        before = self.state()
        bad = self.root / 'bad'
        shutil.copytree(FIX / 'consumer-v1/ties', bad)
        (bad / 'chunks/000000.json').write_text('[]')
        self.cli('export-browser', '--bundle', bad, '--output', self.output, code=1)
        self.assertEqual(before, self.state())
        (self.output / 'unrelated.txt').write_text('preserve')
        self.cli('export-browser', '--bundle', FIX / 'consumer-v1/empty', '--output', self.output, code=1)
        self.assertEqual((self.output / 'unrelated.txt').read_text(), 'preserve')
        for name, state in before.items():
            self.assertEqual(state, self.state()[name])

    def test_concurrent_writer_rejected_before_changes(self):
        self.project()
        before = self.state()
        with writer_lock(self.root / '.data-browser-lock'):
            self.cli('export-browser', '--bundle', FIX / 'consumer-v1/empty', '--output', self.output, code=1)
        self.assertEqual(before, self.state())

    def test_interruption_before_pointer_keeps_old_files_and_retry_recovers(self):
        self.project()
        before = self.state()
        replace = Path.replace
        def fail_pointer(path, target):
            if path.name == 'latest.json':
                raise OSError('simulated interruption')
            return replace(path, target)
        with patch.object(Path, 'replace', fail_pointer):
            with self.assertRaises(OSError):
                export_browser(FIX / 'consumer-v1/empty', self.output)
        for name, state in before.items():
            self.assertEqual(state, self.state()[name])
        with self.assertRaises(DataError):
            verify_browser(self.output)  # Orphans prevent publication.
        self.project('empty')
        self.assertEqual(verify_browser(self.output)['records'], 0)

    def test_corruption_missing_files_and_resealed_bad_index_rejected(self):
        self.project()
        latest = json.loads((self.output / 'latest.json').read_bytes())
        original = json.loads((self.output / latest['index']['url']).read_bytes())
        for mutation in ('order', 'count', 'date', 'url', 'bool', 'extra'):
            with self.subTest(mutation=mutation):
                self.project()
                index = json.loads(json.dumps(original))
                if mutation == 'order': index['chunks'].reverse()
                if mutation == 'count': index['counts']['discovery_export'] += 1
                if mutation == 'date': index['date_counts'][0][0] -= 1
                if mutation == 'url': index['chunks'][0]['url'] = '../private.json'
                if mutation == 'bool': index['browser_contract_version'] = True
                if mutation == 'extra': index['full'] = {}
                raw = (json.dumps(index) + '\n').encode()
                sha = hashlib.sha256(raw).hexdigest()
                (self.output / latest['index']['url']).unlink()
                filename = 'index-' + sha + '.json'
                (self.output / filename).write_bytes(raw)
                (self.output / 'latest.json').write_text(json.dumps({'browser_contract_version': 1,
                    'index': {'url': filename, 'sha256': sha, 'bytes': len(raw)}}))
                self.cli('verify-browser', '--directory', self.output, code=1)
        self.project()
        chunk = self.output / original['chunks'][0]['url']
        chunk.write_text('[]')
        self.cli('verify-browser', '--directory', self.output, code=1)
        chunk.unlink()
        self.cli('verify-browser', '--directory', self.output, code=1)

    def test_real_refresh_corrections_removals_and_date_reordering(self):
        store = self.root / 'store'
        orbits, dates = self.root / 'orbits', self.root / 'dates'
        orbits.write_bytes((FIX / 'MPCORB.DAT').read_bytes())
        dates.write_bytes((FIX / 'NumberedMPs.txt').read_bytes())
        def generate():
            self.cli('refresh', '--store', store, '--mpcorb', orbits, '--numbered', dates, '--allow-count-decrease')
            export = self.cli('export', '--store', store, '--output', self.root / 'exports')
            indexed = self.cli('export-indexed', '--export', export['path'], '--output', self.root / 'indexed', '--chunk-bytes', 300)
            result = self.cli('export-browser', '--bundle', indexed['path'], '--output', self.output)
            expected = json.loads((Path(export['path']) / 'catalog.json').read_bytes())
            self.assertEqual(self.rows(), expected)
            return result, expected
        first, before = generate()
        dates.write_text(dates.read_text().replace('1801 01 01', '2001 01 01'))
        lines = orbits.read_text().splitlines(keepends=True)
        lines[ next(i for i, line in enumerate(lines) if line.startswith('00001')) ] = next(
            line[:92] + '  2.8000000' + line[103:] for line in lines if line.startswith('00001'))
        orbits.write_text(''.join(line for line in lines if not line.startswith('00002')))
        second, after = generate()
        self.assertEqual(len(after), len(before) - 1)
        self.assertNotEqual(first['pin'], second['pin'])
        self.assertTrue(second['changes']['deleted'])
        self.assertTrue(any(row['a'] == 2.8 for row in after))
        # Reintroducing the original source exercises additions and another date reorder.
        orbits.write_bytes((FIX / 'MPCORB.DAT').read_bytes())
        dates.write_bytes((FIX / 'NumberedMPs.txt').read_bytes())
        _, restored = generate()
        self.assertEqual(restored, before)
