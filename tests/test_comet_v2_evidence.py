"""Independent Horizons midpoint checks against committed, final serialized values."""
import gzip
import json
import math
from pathlib import Path
import unittest
from orrery_data import comets_v2 as comets

ROOT = Path(__file__).resolve().parents[1]

class NumericalEvidence(unittest.TestCase):
    def test_every_midpoint_and_preserved_original_series(self):
        root = ROOT / 'data/comets-v2'
        index = json.loads((root / json.loads((root / 'latest.json').read_text())['index']['url']).read_text())
        samples = json.loads(gzip.decompress((ROOT / 'tests/fixtures/comet-v2-midpoints.json.gz').read_bytes()))
        old = ROOT / 'data/comets'
        seed_index = json.loads((old / json.loads((old / 'latest.json').read_text())['index']['url']).read_text())
        for entry in index['objects']:
            series = json.loads((root / entry['data']['url']).read_text())['series']
            check = samples[entry['id'] + '-midpoints']
            self.assertEqual(check['source'], entry['provenance']['midpoints'])
            self.assertEqual(len(check['rows']), comets.COUNT - 1)
            errors = []
            for i, row in enumerate(check['rows']):
                self.assertEqual(row[0], comets.START + 2 + i * comets.STEP)
                errors.append(math.dist(comets.interpolate(series, row[0]), row[1:]))
            self.assertEqual(max(errors), entry['validation']['max_error_au'])
            self.assertLessEqual(max(errors), comets.TOLERANCE)
            if entry['id'] in ('1P','2P','55P'):
                seed = next(v for v in seed_index['objects'] if v['id'] == entry['id'])
                self.assertEqual(entry['provenance'], seed['provenance'])
                self.assertEqual(series, json.loads((old / seed['data']['url']).read_text())['series'])
