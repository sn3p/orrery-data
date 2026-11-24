"""Unpublished research boundaries: real source parsing and metadata provenance."""
import copy
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from scripts import comet_history_pilot as pilot
from orrery_data.formats import DataError


class HistoryPilotTests(unittest.TestCase):
    def test_scientific_context_change_is_not_normalized_away(self):
        header = 'Start time: old\nStop  time: old\nCenter body: DE441\nEOP file: old'
        self.assertEqual(pilot.model_identity(header), pilot.model_identity(header.replace('time: old', 'time: new')))
        for old, new in [('DE441', 'DE440'), ('EOP file: old', 'EOP file: new')]:
            self.assertNotEqual(pilot.model_identity(header), pilot.model_identity(header.replace(old, new)))

    def test_velocity_uses_hermite_derivative(self):
        # Analytic p(t)=t^3 across a four-day interval, not a linear approximation.
        series = {'start': 100, 'step': 4, 'states': [[0,0,0,0,0,0], [64,0,0,48,0,0]]}
        self.assertEqual(pilot.velocity(series, 102), [12,0,0])
        self.assertEqual(pilot.velocity(series, 104), [48,0,0])
        with self.assertRaises(DataError):
            pilot.velocity(series, 105)

    def test_actual_response_and_wrong_science_rejection(self):
        raw = (pilot.ROOT / 'tests/fixtures/horizons-1p.json').read_bytes().replace(b'2100-Jan-01', b'1980-Jan-09')
        rows, header = pilot.parse(raw, '1P/Halley', 'JPL#75', 2444239.5, 3)
        self.assertEqual(len(rows), 3)
        for old, new in [('JPL#75', 'JPL#76'), ('Ecliptic of J2000.0', 'ICRF'),
                         ('JDUT', 'JDTDB'), ('2444243.500000000', '2444239.500000000'),
                         ('$$EOE', 'missing')]:
            with self.subTest(old=old), self.assertRaises((DataError, ValueError)):
                pilot.parse(raw.replace(old.encode(), new.encode()), '1P/Halley', 'JPL#75', 2444239.5, 3)
        with self.assertRaises(DataError):
            pilot.parse(raw, '1P/Halley', 'JPL#75', 2444239.5, 4)

    def test_metadata_preserves_facts_and_precision(self):
        value = pilot.validate_metadata(pilot.ROOT / 'research/comet-history')
        halley = value['objects'][0]
        self.assertEqual(halley['catalogue_discovery']['date'], '1758-Dec-25')
        self.assertEqual([e['kind'] for e in halley['events']], ['earlier_sighting', 'recovery'])
        self.assertEqual(halley['events'][0]['date']['year'], -239)
        self.assertIsNone(halley['events'][0]['date']['day'])
        tt = value['objects'][2]
        self.assertEqual(sum(e['kind'] == 'independent_discovery' for e in tt['events']), 2)

    def test_metadata_rejects_invented_precision_and_changed_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp) / 'metadata'
            shutil.copytree(pilot.ROOT / 'research/comet-history', directory)
            path = directory / 'discoveries.json'; original = json.loads(path.read_text())
            for mutate in [lambda v: v['objects'][0]['events'][0]['date'].update(day=1),
                           lambda v: v['objects'][0]['catalogue_discovery'].update(date='1705-Jan-01'),
                           lambda v: v['objects'][1]['events'][0]['date'].update(day=32),
                           lambda v: v['objects'][1]['events'][0].pop('date'),
                           lambda v: v['objects'][0]['catalogue_provenance'].update(raw_file='../outside')]:
                value = copy.deepcopy(original); mutate(value); path.write_text(json.dumps(value))
                with self.assertRaises(DataError):
                    pilot.validate_metadata(directory)

    def test_offline_cache_missing_and_corruption_never_downloads(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(pilot, 'OUTPUT', Path(tmp)), patch.object(pilot, 'download') as download:
            with self.assertRaises(DataError):
                pilot.acquire('https://example.invalid/', False, bytes)
            download.assert_not_called()
            download.return_value = b'raw'
            raw, info, parsed = pilot.acquire('https://example.invalid/', True, bytes)
            self.assertEqual(pilot.acquire('https://example.invalid/', False, bytes), (raw, info, parsed))
            (Path(tmp) / 'raw' / (info['sha256'] + '.raw')).write_bytes(b'corrupt')
            with self.assertRaises(DataError):
                pilot.acquire('https://example.invalid/', False, bytes)

    def test_bad_download_never_commits_and_next_acquisition_recovers(self):
        raw = (pilot.ROOT / 'tests/fixtures/horizons-1p.json').read_bytes().replace(b'2100-Jan-01', b'1980-Jan-09')
        validate = lambda b: pilot.parse(b, '1P/Halley', 'JPL#75', 2444239.5, 3)
        with tempfile.TemporaryDirectory() as tmp, patch.object(pilot, 'OUTPUT', Path(tmp)), patch.object(pilot, 'download') as download:
            for bad in (b'{"error":"bad"}', raw[:-100], b'[]'):
                download.return_value = bad
                with self.assertRaises(DataError):
                    pilot.acquire('https://example.invalid/', True, validate)
                self.assertFalse(list((Path(tmp) / 'raw').glob('*.json')))
            download.return_value = raw
            pilot.acquire('https://example.invalid/', True, validate)
            record = next((Path(tmp) / 'raw').glob('*.json'))
            for bad in ('{}', '[]', '{bad'):
                record.write_text(bad)
                with self.assertRaisesRegex(DataError, 'Corrupt cache'):
                    pilot.acquire('https://example.invalid/', False, validate)
                pilot.acquire('https://example.invalid/', True, validate)
            info = json.loads(record.read_text())
            (Path(tmp) / 'raw' / (info['sha256'] + '.raw')).unlink()
            with self.assertRaisesRegex(DataError, 'Corrupt cache'):
                pilot.acquire('https://example.invalid/', False, validate)
            pilot.acquire('https://example.invalid/', True, validate)

    def test_concurrent_acquisition_and_run_fail_before_work(self):
        from threading import Event
        from concurrent.futures import ThreadPoolExecutor
        entered, release = Event(), Event()
        def download(url):
            entered.set()
            if not release.wait(5):
                raise AssertionError('test synchronization timed out')
            return b'valid'
        with tempfile.TemporaryDirectory() as tmp, patch.object(pilot, 'OUTPUT', Path(tmp)), patch.object(pilot, 'download', side_effect=download):
            with ThreadPoolExecutor(max_workers=1) as pool:
                first = pool.submit(pilot.acquire, 'url', True, bytes)
                try:
                    self.assertTrue(entered.wait(5))
                    with self.assertRaisesRegex(DataError, 'Another writer'):
                        pilot.acquire('url', True, bytes)
                finally:
                    release.set()
                first.result()
            with pilot.writer_lock(Path(tmp)), patch.object(pilot, '_run_locked') as work:
                with self.assertRaisesRegex(DataError, 'Another writer'):
                    pilot.run()
                work.assert_not_called()

    def test_baseline_is_pinned_verified_and_retained_for_replay(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(pilot, 'OUTPUT', Path(tmp)):
            pin, first = pilot.load_baseline()
            with patch.object(pilot.subprocess, 'run', side_effect=AssertionError('retained replay must not read Git or latest')):
                other = Path(tmp) / 'other-checkout'
                (other / 'research/comet-history').mkdir(parents=True)
                shutil.copy2(pilot.ROOT / 'research/comet-history/baseline.json', other / 'research/comet-history/baseline.json')
                with patch.object(pilot, 'ROOT', other):
                    self.assertEqual(pilot.load_baseline(), (pin, first))
            path = Path(tmp) / 'baseline' / (pin['index']['sha256'] + '.json')
            path.write_bytes(path.read_bytes() + b' ')
            with self.assertRaisesRegex(DataError, 'Corrupt baseline'):
                pilot.load_baseline()

    def test_pass_requires_baseline_position_and_velocity(self):
        checks = [{'max_error_au': 0}]
        self.assertTrue(pilot.acceptance(checks, 0, 0))
        self.assertFalse(pilot.acceptance(checks, pilot.BASELINE_POSITION_LIMIT * 2, 0))
        self.assertFalse(pilot.acceptance(checks, 0, pilot.BASELINE_VELOCITY_LIMIT * 2))
        self.assertFalse(pilot.acceptance(checks, float('nan'), 0))

    def test_run_rejects_baseline_drift_even_when_interpolation_passes(self):
        from urllib.parse import parse_qs, urlsplit
        def acquire(url, online, validate):
            q = parse_qs(urlsplit(url).query)
            start = float(q['START_TIME'][0].strip("'")[2:])
            end = float(q['STOP_TIME'][0].strip("'")[2:])
            rows = [[start + 4*i, 0,0,0,0,0,0] for i in range(round((end-start)/4)+1)]
            return b'', {}, (rows, 'same scientific model')
        baseline = {'start': pilot.jd('1980-01-01'), 'step': 4,
                    'states': [[1,0,0,0,0,0], [0,0,0,0,0,0]]}
        with tempfile.TemporaryDirectory() as tmp, patch.object(pilot, 'OUTPUT', Path(tmp)), \
                patch.object(pilot, 'validate_metadata', return_value={}), \
                patch.object(pilot, 'CASES', {'1P': ('1P/Halley','90000030','JPL#75','1980-01-01')}), \
                patch.object(pilot, 'load_baseline', return_value=({}, {'1P': (baseline, {})})), \
                patch.object(pilot, 'acquire', side_effect=acquire), patch('builtins.print'):
            with self.assertRaisesRegex(DataError, 'baseline compatibility'):
                pilot.run()
            result = json.loads((Path(tmp) / 'results.json').read_text())['1P']
            self.assertFalse(result['passed'])
            self.assertEqual(result['overlap_max_difference_au'], 1)
            self.assertTrue(all(c['max_error_au'] == 0 for c in result['checks']))

    def test_baseline_size_measures_exact_bytes(self):
        import hashlib
        raw = b'{ "unusual": [1.000] }\n'
        with patch.object(pilot, 'encode', side_effect=AssertionError('must not encode')):
            measured = pilot.raw_size(raw)
        self.assertEqual(measured['json_bytes'], len(raw))
        self.assertEqual(measured['sha256'], hashlib.sha256(raw).hexdigest())

    def test_query_is_pinned_and_reference_grid_ends_inside_last_interval(self):
        from urllib.parse import parse_qs, urlsplit
        q = parse_qs(urlsplit(pilot.query('90000030', 2391511.5, 10, 3)).query)
        self.assertEqual(q['COMMAND'], ["'90000030;'"])
        self.assertEqual(q['START_TIME'], ["'JD2391514.5'"])
        self.assertEqual(q['STOP_TIME'], ["'JD2391546.5'"])


if __name__ == '__main__':
    unittest.main()
