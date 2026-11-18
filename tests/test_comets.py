"""Offline numerical, acquisition, CLI, persistence and site-composition boundaries."""

import copy
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

from orrery_data import comets, horizons
from orrery_data.browser import sync_candidate
from orrery_data.formats import DataError
from orrery_data.storage import read_json, write_json, writer_lock

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / 'tests/fixtures/horizons-1p.json'


class CometTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.output = self.root / 'data/comets'
        self.output.parent.mkdir()
        shutil.copytree(ROOT / 'data/comets', self.output)

    def test_committed_projection_and_fresh_cli(self):
        result = comets.verify_comets(self.output)
        self.assertEqual(result['objects'], 3)
        run = subprocess.run([sys.executable, '-m', 'orrery_data', 'verify-comets', '--directory', str(self.output)],
                             capture_output=True, text=True)
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(json.loads(run.stdout), result)
        index = read_json(self.output / result['pin']['url'])
        for entry in index['objects']:
            series = read_json(self.output / entry['data']['url'])['series']
            for jd, expected in [(entry['coverage'][0], series['states'][0][:3]),
                                 (entry['coverage'][1], series['states'][-1][:3])]:
                self.assertEqual(comets.interpolate(series, jd), expected)
            for jd in [entry['coverage'][0] - 1, entry['coverage'][1] + 1, float('nan')]:
                with self.assertRaises(ValueError): comets.interpolate(series, jd)

    def test_corruption_unknown_files_symlinks_and_versions(self):
        latest = self.output / 'latest.json'
        original = latest.read_bytes()
        for value in [True, 2, '1']:
            pointer = json.loads(original); pointer['comet_contract_version'] = value
            write_json(latest, pointer)
            with self.assertRaises(DataError): comets.verify_comets(self.output)
        latest.write_bytes(original)
        extra = self.output / 'surprise'; extra.write_text('x')
        with self.assertRaises(DataError): comets.verify_comets(self.output)
        extra.unlink(); extra.symlink_to(latest)
        with self.assertRaises(DataError): comets.verify_comets(self.output)
        extra.unlink()
        asset = next((self.output / 'objects').iterdir()); asset.write_text('{}')
        with self.assertRaises(DataError): comets.verify_comets(self.output)

    def test_horizons_fixture_and_malformed_responses(self):
        envelope = read_json(FIXTURE)
        raw = json.dumps(envelope).encode()
        with patch.object(horizons, 'COUNT', 3):
            rows, source = horizons.parse_response(raw, '1P')
            self.assertEqual(len(rows), 3)
            self.assertEqual(source['solution'], 'JPL#75')
            for old, new in [('1P/Halley', '2P/Encke'), ('AU-D', 'KM-S'), ('JDUT', 'JDTDB'),
                             ('Ecliptic of J2000.0', 'ICRF'), ('GEOMETRIC', 'APPARENT'),
                             ('$$EOE', ''), ('2444239.500000000', '2444240.500000000')]:
                with self.subTest(old=old), self.assertRaises(ValueError):
                    horizons.parse_response(raw.replace(old.encode(), new.encode()), '1P')
            for malformed in [dict(envelope, error='No ephemeris'), dict(envelope, signature={'version': '9'}),
                              dict(envelope, result=envelope['result'].replace('$$EOE', '$$SOE$$EOE'))]:
                with self.assertRaises(ValueError): horizons.parse_response(json.dumps(malformed).encode(), '1P')
        with self.assertRaises(DataError): horizons.parse_response(raw, '1P')

    def test_bounded_serial_http_retry(self):
        class Response(io.BytesIO):
            status = 200
            headers = {}
        calls = []
        def opening(request, timeout):
            calls.append(request)
            if len(calls) < 3: raise HTTPError(request.full_url, 429, 'rate limit', {}, None)
            return Response(b'{}')
        with patch.object(horizons, 'urlopen', side_effect=opening), patch.object(horizons.time, 'sleep') as sleep:
            self.assertEqual(horizons.download(horizons.query('1P')), b'{}')
            self.assertEqual(sleep.call_count, 2)
            self.assertIn('orrery-data/', calls[0].get_header('User-agent'))
            self.assertIn(horizons.CONTACT, calls[0].get_header('User-agent'))
        with patch.object(horizons, 'urlopen', return_value=Response(b'x' * (horizons.MAX_RESPONSE + 1))):
            with self.assertRaises(DataError): horizons.download(horizons.query('1P'))

    def test_failed_acquisition_and_competing_writer_preserve_product(self):
        before = {p.relative_to(self.output): p.read_bytes() for p in self.output.rglob('*') if p.is_file()}
        with patch.object(comets, 'acquire', side_effect=DataError('second object failed')):
            with self.assertRaises(DataError): comets.refresh_comets(self.root / 'cache', self.output)
        with writer_lock(self.root / '.data-browser-lock'), patch.object(comets, 'acquire') as acquire:
            with self.assertRaises(DataError): comets.refresh_comets(self.root / 'cache', self.output)
            acquire.assert_not_called()
        after = {p.relative_to(self.output): p.read_bytes() for p in self.output.rglob('*') if p.is_file()}
        self.assertEqual(before, after)
        comets.verify_comets(self.output)

    def test_cache_replay_corruption_and_offline_missing(self):
        cache = self.root / 'cache'
        with patch.object(horizons, 'TARGETS', {'1P': '1P/Halley'}), \
             patch.object(horizons, 'download', return_value=b'raw-response') as download, \
             patch.object(horizons, 'parse_response', return_value=([], {})):
            horizons.acquire(cache)
            self.assertEqual(download.call_count, 2)
            before = {p.name: p.read_bytes() for p in cache.glob('*.json')}
            horizons.acquire(cache, offline=True)
            self.assertEqual(download.call_count, 2)
            self.assertEqual(before, {p.name: p.read_bytes() for p in cache.glob('*.json')})
            next(cache.glob('*.raw')).write_bytes(b'corrupt')
            with self.assertRaises(DataError): horizons.acquire(cache, offline=True)
            with self.assertRaises(DataError): horizons.acquire(self.root / 'missing', offline=True)
            self.assertEqual(download.call_count, 2)

    def test_candidate_determinism_and_scientific_provenance_changes(self):
        pin = read_json(self.output / 'latest.json')['index']
        index = read_json(self.output / pin['url'])
        records = {}
        for entry in index['objects']:
            series = read_json(self.output / entry['data']['url'])['series']
            rows = [[series['start'] + i * series['step'], *row] for i, row in enumerate(series['states'])]
            middle = [[series['start'] + i * series['step'] + 2,
                       *comets.interpolate(series, series['start'] + i * series['step'] + 2), 0, 0, 0]
                      for i in range(len(rows) - 1)]
            records[entry['id'] + '-vectors'] = (rows, copy.deepcopy(entry['provenance']['vectors']))
            records[entry['id'] + '-midpoints'] = (middle, copy.deepcopy(entry['provenance']['midpoints']))
        original_build = comets.build_candidate
        def checked_stage(records, stage, previous):
            self.assertFalse(stage.is_relative_to(self.output.parent))
            return original_build(records, stage, previous)
        with patch.object(comets, 'acquire', return_value=records), patch.object(comets, 'build_candidate', side_effect=checked_stage):
            first = comets.refresh_comets(self.root / 'cache', self.output)
            for _, source in records.values():
                source['retrieved_at'] = '2026-11-19T00:00:00Z'
                source['sha256'] = '0' * 64
            same = comets.refresh_comets(self.root / 'cache', self.output)
            self.assertEqual(same['status'], 'unchanged')
            self.assertEqual(first['pin'], same['pin'])
            records['1P-vectors'][1]['header'] += '\nModel changed'
            changed = comets.refresh_comets(self.root / 'cache', self.output)
            self.assertEqual(changed['status'], 'updated')
            self.assertNotEqual(changed['pin'], same['pin'])

    def test_pointer_last_and_interrupted_orphan_recovery(self):
        stage = self.root / 'candidate'; shutil.copytree(self.output, stage)
        orphan = self.output / 'objects' / ('0' * 64 + '.json'); orphan.write_text('{}')
        changes = sync_candidate(stage, self.output, inventory=comets.managed_files)
        self.assertEqual(changes['deleted'], ['objects/' + orphan.name])
        self.assertEqual(changes['changed'], [])
        comets.verify_comets(self.output)



if __name__ == '__main__': unittest.main()
