"""Catalogue membership is data; scientific schema and identity remain strict."""
import copy
import gzip
import io
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from orrery_data import comets
from orrery_data.browser import reference
from orrery_data.cli import main
from orrery_data.formats import DataError
from orrery_data.storage import read_json, write_json

ROOT = Path(__file__).resolve().parents[1]

class Catalogue(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.output = self.root / 'data/comets'
        shutil.copytree(ROOT / 'tests/fixtures/comets-schema2', self.output)
        self.index = read_json(self.output / read_json(self.output / 'latest.json')['index']['url'])

    def install_index(self, index):
        temp = self.output / 'index.json'
        write_json(temp, index)
        pin = reference(temp, 'index-', '.json')
        temp.rename(self.output / pin['url'])
        write_json(self.output / 'latest.json', {'comet_contract_version': 2, 'index': pin})
        return comets.verify_comets(self.output, allow_orphans=True)

    def test_catalogue_membership_and_order_are_data(self):
        for entries in [self.index['objects'][:1], self.index['objects'][::-1]]:
            self.assertEqual(self.install_index(dict(self.index, objects=entries))['objects'], len(entries))
        # Synthetic contract fixture only: copied trajectory does NOT establish a real new comet.
        entry = copy.deepcopy(self.index['objects'][0])
        value = read_json(self.output / entry['data']['url'])
        value['id'] = entry['id'] = '999P'
        entry['name'] = '999P/Synthetic fixture'
        for kind, source in entry['provenance'].items():
            source['target'] = entry['name']
            source['url'] = comets.query(entry['id'], kind == 'midpoints')
        temp = self.output / 'object.json'
        write_json(temp, value)
        entry['data'] = reference(temp, 'objects/', '.json')
        temp.rename(self.output / entry['data']['url'])
        self.assertEqual(self.install_index(dict(self.index, objects=[*self.index['objects'], entry]))['objects'], 7)

    def test_invalid_catalogues_fail_after_rehashing(self):
        cases = [[], [self.index['objects'][0]] * 2]
        for key, value in [('id','01P'), ('id','1P;'), ('id','1P\n'), ('id',True), ('name','2P/Wrong'),
                           ('name','1P/'), ('name','1P/Bad\nname'), ('name','1P/Name ')]:
            entry = copy.deepcopy(self.index['objects'][0]); entry[key] = value
            cases.append([entry])
        entry = copy.deepcopy(self.index['objects'][0]); entry['provenance']['vectors']['target'] = '1P/Wrong'
        cases.append([entry])
        for entries in cases:
            with self.subTest(entries=str(entries)[:80]), self.assertRaises(DataError):
                self.install_index(dict(self.index, objects=entries))

    def test_refresh_cli_grows_shrinks_and_reorders_existing_product(self):
        checks = json.loads(gzip.decompress((ROOT / 'tests/fixtures/comet-midpoints.json.gz').read_bytes()))
        records = {}
        for entry in self.index['objects']:
            series = read_json(self.output / entry['data']['url'])['series']
            records[entry['id']+'-vectors'] = ([[comets.START+i*comets.STEP,*row] for i,row in enumerate(series['states'])], entry['provenance']['vectors'])
            records[entry['id']+'-midpoints'] = (checks[entry['id']+'-midpoints']['rows'], entry['provenance']['midpoints'])
        targets = comets.TARGETS.copy()
        for selection in [dict(list(targets.items())[:3]), targets, dict(reversed(list(targets.items())))]:
            with patch.object(comets.PRODUCT, 'targets', selection), patch.object(comets.PRODUCT, 'acquire', return_value=records), patch('sys.stdout', new_callable=io.StringIO):
                self.assertEqual(main(['refresh-comets','--offline','--cache',str(self.root/'cache'),'--output',str(self.output)]), 0)
            result = comets.verify_comets(self.output)
            index = read_json(self.output / result['pin']['url'])
            self.assertEqual([e['id'] for e in index['objects']], list(selection))
