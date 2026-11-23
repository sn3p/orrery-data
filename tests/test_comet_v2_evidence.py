"""Independent numerical evidence can follow refreshed v2 data without changing v1."""
import copy
import gzip
import hashlib
import json
import math
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from orrery_data import comet_product, comets_v2 as comets
from orrery_data.browser import reference
from orrery_data.horizons import parse_response
from orrery_data.storage import read_json, write_json

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / 'tests/fixtures/comet-v2-midpoints.json.gz'
SCRIPT = ROOT / 'scripts/rebuild_comet_evidence.py'


def product_index(root):
    return read_json(root / read_json(root / 'latest.json')['index']['url'])


class NumericalEvidence(unittest.TestCase):
    def test_every_midpoint_matches_current_published_product(self):
        root = ROOT / 'data/comets-v2'
        index = product_index(root)
        samples = json.loads(gzip.decompress(FIXTURE.read_bytes()))
        self.assertEqual(set(samples), {entry['id'] + '-midpoints' for entry in index['objects']})
        for entry in index['objects']:
            series = read_json(root / entry['data']['url'])['series']
            check = samples[entry['id'] + '-midpoints']
            self.assertEqual(check['source'], entry['provenance']['midpoints'])
            self.assertEqual(len(check['rows']), comets.COUNT - 1)
            errors = []
            for i, row in enumerate(check['rows']):
                self.assertEqual(row[0], comets.START + 2 + i * comets.STEP)
                errors.append(math.dist(comets.interpolate(series, row[0]), row[1:]))
            worst = max(range(len(errors)), key=errors.__getitem__)
            self.assertEqual(errors[worst], entry['validation']['max_error_au'])
            self.assertEqual(check['rows'][worst][0], entry['validation']['worst_jd'])
            self.assertLessEqual(max(errors), comets.TOLERANCE)

    def test_seed_projection_preserves_supplied_series_and_provenance(self):
        # Preservation is a producer property, not a requirement that future v2
        # refreshes remain equal to the independently managed current v1 product.
        root = ROOT / 'data/comets'
        index = product_index(root)
        records, originals = {}, {}
        for entry in index['objects']:
            series = read_json(root / entry['data']['url'])['series']
            originals[entry['id']] = series
            records[entry['id'] + '-vectors'] = (
                [[comets.START + i * comets.STEP, *row] for i, row in enumerate(series['states'])],
                entry['provenance']['vectors'])
            # Synthetic midpoint controls test preservation only, not source accuracy.
            records[entry['id'] + '-midpoints'] = (
                [[comets.START + 2 + i * comets.STEP,
                  *comets.interpolate(series, comets.START + 2 + i * comets.STEP)]
                 for i in range(comets.COUNT - 1)], entry['provenance']['midpoints'])
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp)
            comet_product.build_candidate(records, output, contract=comets.CONTRACT,
                                          targets={entry['id']: entry['name'] for entry in index['objects']})
            for entry, seed in zip(product_index(output)['objects'], index['objects']):
                self.assertEqual(entry['provenance'], seed['provenance'])
                self.assertEqual(read_json(output / entry['data']['url'])['series'], originals[entry['id']])

    def test_rebuild_cli_follows_refreshed_series_and_provenance_and_preserves_failed_output(self):
        # Full-sized synthetic raw tables exercise the real offline parser/CLI.
        # They are never committed as scientific data or substituted for real evidence.
        original = product_index(ROOT / 'data/comets-v2')['objects'][0]
        object_id = original['id']
        series = copy.deepcopy(read_json(ROOT / 'data/comets-v2' / original['data']['url'])['series'])
        series['states'][0][0] += 1e-8
        middle = json.loads(gzip.decompress(FIXTURE.read_bytes()))[object_id + '-midpoints']['rows']
        targets = {object_id: original['name']}
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); cache = root / 'cache'; cache.mkdir()
            output = root / 'data/comets-v2'; output.mkdir(parents=True)
            records = {}
            raw_paths = []
            for kind, rows in [
                ('vectors', [[comets.START + i * comets.STEP, *row] for i, row in enumerate(series['states'])]),
                ('midpoints', [[*row, 0, 0, 0] for row in middle])]:
                source = copy.deepcopy(original['provenance'][kind])
                header = source['header'].replace(source['solution'], 'JPL#Synthetic/1')
                body = '\n'.join(f"{row[0]}, unused calendar, " + ', '.join(map(str, row[1:])) + ',' for row in rows)
                raw = json.dumps({'signature': {'source': 'NASA/JPL Horizons API', 'version': source['api_version']},
                                  'result': header + '\n$$SOE\n' + body + '\n$$EOE\n'}).encode()
                parsed, metadata = parse_response(raw, object_id, kind == 'midpoints', targets=targets)
                source.update(metadata, sha256=hashlib.sha256(raw).hexdigest(), retrieved_at='2026-11-22T00:00:00Z')
                raw_path = cache / (source['sha256'] + '.raw'); raw_path.write_bytes(raw); raw_paths.append(raw_path)
                records[object_id + '-' + kind] = (parsed, source)
            comet_product.build_candidate(records, output, contract=comets.CONTRACT, targets=targets)
            fixture = root / 'evidence.json.gz'; fixture.write_bytes(b'previous evidence')
            command = [sys.executable, str(SCRIPT), '--directory', str(output), '--cache', str(cache), '--output', str(fixture)]
            result = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            first = fixture.read_bytes()
            saved = json.loads(gzip.decompress(first))
            self.assertEqual(saved[object_id + '-midpoints']['source']['solution'], 'JPL#Synthetic/1')
            self.assertEqual(saved[object_id + '-midpoints']['rows'], middle)
            self.assertEqual(subprocess.run(command, capture_output=True).returncode, 0)
            self.assertEqual(fixture.read_bytes(), first)
            original_raw = raw_paths[0].read_bytes()
            raw_paths[0].write_bytes(b'corrupt')
            self.assertNotEqual(subprocess.run(command, capture_output=True).returncode, 0)
            self.assertEqual(fixture.read_bytes(), first)
            raw_paths[0].unlink()
            self.assertNotEqual(subprocess.run(command, capture_output=True).returncode, 0)
            self.assertEqual(fixture.read_bytes(), first)
            retained = root / 'retained.raw'
            retained.write_bytes(original_raw)
            raw_paths[0].symlink_to(retained)
            self.assertNotEqual(subprocess.run(command, capture_output=True).returncode, 0)
            self.assertEqual(fixture.read_bytes(), first)
            raw_paths[0].unlink()
            # A validly hashed/parsed raw response still must match the published states.
            changed = json.loads(original_raw)
            x = records['1P-vectors'][0][0][1]
            changed['result'] = changed['result'].replace(str(x), str(x + 1e-5), 1)
            raw = json.dumps(changed).encode()
            raw_hash = hashlib.sha256(raw).hexdigest()
            (cache / (raw_hash + '.raw')).write_bytes(raw)
            old_pin = read_json(output / 'latest.json')['index']
            index = read_json(output / old_pin['url'])
            index['objects'][0]['provenance']['vectors']['sha256'] = raw_hash
            temporary = output / 'index.json'; write_json(temporary, index)
            pin = reference(temporary, 'index-', '.json')
            temporary.rename(output / pin['url'])
            (output / old_pin['url']).unlink()
            write_json(output / 'latest.json', {'comet_contract_version': 2, 'index': pin})
            result = subprocess.run(command, capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('pinned vectors differ from serialized states', result.stderr)
            self.assertEqual(fixture.read_bytes(), first)
