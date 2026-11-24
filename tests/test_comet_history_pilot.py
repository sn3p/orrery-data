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
        raw = (pilot.ROOT / 'tests/fixtures/horizons-1p.json').read_bytes()
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
                           lambda v: v['objects'][0]['catalogue_discovery'].update(date='1705-Jan-01')]:
                value = copy.deepcopy(original); mutate(value); path.write_text(json.dumps(value))
                with self.assertRaises(DataError):
                    pilot.validate_metadata(directory)

    def test_offline_cache_missing_and_corruption_never_downloads(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(pilot, 'OUTPUT', Path(tmp)), patch.object(pilot, 'download') as download:
            with self.assertRaises(DataError):
                pilot.acquire('https://example.invalid/', False)
            download.assert_not_called()
            download.return_value = b'raw'
            raw, info = pilot.acquire('https://example.invalid/', True)
            self.assertEqual(pilot.acquire('https://example.invalid/', False), (raw, info))
            (Path(tmp) / 'raw' / (info['sha256'] + '.raw')).write_bytes(b'corrupt')
            with self.assertRaises(DataError):
                pilot.acquire('https://example.invalid/', False)

    def test_query_is_pinned_and_reference_grid_ends_inside_last_interval(self):
        from urllib.parse import parse_qs, urlsplit
        q = parse_qs(urlsplit(pilot.query('90000030', 2391511.5, 10, 3)).query)
        self.assertEqual(q['COMMAND'], ["'90000030;'"])
        self.assertEqual(q['START_TIME'], ["'JD2391514.5'"])
        self.assertEqual(q['STOP_TIME'], ["'JD2391546.5'"])


if __name__ == '__main__':
    unittest.main()
