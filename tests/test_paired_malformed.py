"""Malformed paired payloads fail validation after their hashes are authenticated."""

import copy
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from orrery_data import paired
from orrery_data.formats import DataError
from orrery_data.storage import json_bytes, read_json, write_json
from scripts import named_body_candidate, update_browser


ROOT = Path(__file__).resolve().parents[1]
BUNDLE = ROOT / 'tests/fixtures/consumer-v1/ties'


class MalformedPairedPublication(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        definitions = self.root / 'research/named-bodies'
        definitions.mkdir(parents=True)
        shutil.copyfile(ROOT / 'research/named-bodies/definitions.json', definitions / 'definitions.json')
        for module in (update_browser, named_body_candidate):
            replacement = patch.object(module, 'ROOT', self.root)
            replacement.start()
            self.addCleanup(replacement.stop)
        self.original = self.root / 'original'
        with patch.object(paired, 'published_descriptor', return_value=None):
            update_browser.update_from_bundle(BUNDLE, self.original)

    def copy_site(self, name):
        site = self.root / name
        shutil.copytree(self.original, site)
        pair = read_json(site / 'paired/latest.json')
        generation = read_json(site / 'paired/retention.json')['generations'][0]['id']
        return site, pair, site / 'paired/generations' / generation

    def replace_reference(self, directory, old, value, prefix):
        raw = json_bytes(value)
        digest = hashlib.sha256(raw).hexdigest()
        name = f'{prefix}{digest}.json'
        (directory / old['url']).unlink()
        (directory / name).write_bytes(raw)
        return {'url': name, 'bytes': len(raw), 'sha256': digest}

    def publish_pair(self, site, folder, pair):
        generation = hashlib.sha256(json_bytes([
            pair['asteroids']['sha256'], pair['namedBodies']['sha256']
        ])).hexdigest()
        pair['asteroids']['url'] = (
            paired.BASE + f'paired/asteroids/{pair["asteroids"]["sha256"]}/'
            + f'index-{pair["asteroids"]["sha256"]}.json'
        )
        pair['namedBodies']['url'] = (
            paired.BASE + f'paired/generations/{generation}/named/'
            + f'index-{pair["namedBodies"]["sha256"]}.json'
        )
        write_json(folder / 'pair.json', pair)
        if folder.name != generation:
            folder.rename(folder.parent / generation)
        write_json(site / 'paired/latest.json', pair)
        write_json(site / 'paired/retention.json', {
            'version': 1, 'generations': [{'id': generation, 'superseded_at': None}]
        })

    def assert_clean_failure(self, site, message):
        with self.assertRaisesRegex(DataError, message):
            paired.verify_paired(site)
        commands = (
            [sys.executable, '-m', 'orrery_data', 'verify-site', '--directory', str(site)],
            [sys.executable, str(ROOT / 'scripts/update_browser.py'), '--from-bundle',
             str(BUNDLE), '--output', str(site)],
        )
        for command in commands:
            result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertNotIn('Traceback', result.stderr)
            self.assertRegex(json.loads(result.stderr)['error'], message)

    def test_valid_hash_malformed_chunk_shapes_fail_before_mapping_lookup(self):
        for variant, message in (
            ('object', 'Browser ordinal coverage mismatch'),
            ('short', 'Browser ordinal coverage mismatch'),
            ('missing-disc', 'Invalid discovery catalog fields'),
        ):
            with self.subTest(variant=variant):
                site, pair, folder = self.copy_site(variant)
                asteroids = site / 'paired/asteroids' / pair['asteroids']['sha256']
                latest = read_json(asteroids / 'latest.json')
                index = read_json(asteroids / latest['index']['url'])
                chunk = index['chunks'][0]
                rows = read_json(asteroids / chunk['url'])
                if variant == 'object':
                    rows = {}
                elif variant == 'short':
                    rows = rows[:-1]
                else:
                    del rows[0]['disc']
                chunk.update(self.replace_reference(asteroids, chunk, rows, 'chunks/'))
                latest['index'] = self.replace_reference(asteroids, latest['index'], index, 'index-')
                write_json(asteroids / 'latest.json', latest)
                pair['asteroids'] = copy.deepcopy(latest['index'])
                asteroids.rename(asteroids.parent / pair['asteroids']['sha256'])
                self.publish_pair(site, folder, pair)
                self.assert_clean_failure(site, message)

    def test_valid_hash_out_of_range_mapping_fails_before_chunk_indexing(self):
        site, pair, folder = self.copy_site('mapping')
        named = folder / 'named'
        old = {**pair['namedBodies'], 'url': Path(pair['namedBodies']['url']).name}
        index = read_json(named / old['url'])
        mapping = read_json(named / index['mapping']['url'])
        mapping['members'][0].update(status='present', ordinal=mapping['records'],
                                     discovery_jd=2400000.5, reason=None)
        index['mapping'] = self.replace_reference(named, index['mapping'], mapping, 'mapping-')
        pair['namedBodies'] = self.replace_reference(named, old, index, 'index-')
        self.publish_pair(site, folder, pair)
        self.assert_clean_failure(site, 'present mapping')

    def test_malformed_ledger_fields_fail_without_raw_lookup_errors(self):
        for variant, message in (
            ('entry-array', 'id superseded_at'),
            ('id-array', 'retention identity'),
            ('missing-id', 'id superseded_at'),
            ('timestamp-array', 'current retention timestamp'),
        ):
            with self.subTest(variant=variant):
                site, _, _ = self.copy_site(variant)
                path = site / 'paired/retention.json'
                ledger = read_json(path)
                entry = ledger['generations'][0]
                if variant == 'entry-array':
                    ledger['generations'][0] = []
                elif variant == 'id-array':
                    entry['id'] = []
                elif variant == 'missing-id':
                    del entry['id']
                else:
                    entry['superseded_at'] = []
                write_json(path, ledger)
                self.assert_clean_failure(site, message)
