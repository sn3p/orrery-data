import copy
import importlib.util
import json
from pathlib import Path
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('discovery_metadata', ROOT / 'scripts/comet_discovery_metadata.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


class DiscoveryMetadataTests(unittest.TestCase):
    def test_dates_preserve_precision_and_unknown(self):
        for value in (None, '', 'unknown', '1948-Foo', '1948-??-15', '1900-Feb-29'):
            self.assertIsNone(m.structured_date(value))
        self.assertEqual(m.structured_date('1948')['precision'], 'year')
        self.assertEqual(m.structured_date('1948-Jan')['precision'], 'month')
        self.assertEqual(m.structured_date('1965-Nov-00')['precision'], 'month')
        self.assertEqual(m.structured_date('2000-Feb-29')['day'], 29)
        self.assertEqual(m.structured_date('1812-Jul-21')['calendar'], 'historical-unspecified')

    def test_exact_candidate_rebuild_and_legacy_preservation(self):
        result = m.build()
        expected = json.loads((m.EVIDENCE / 'metadata.json').read_bytes())
        self.assertEqual(result, expected)
        root = ROOT / 'data/comets'
        directory = json.loads((root / json.loads((root/'latest.json').read_bytes())['index']['url']).read_bytes())
        legacy = json.loads((root / directory['legacy']['url']).read_bytes())
        actual = {r['id']: r for r in result['objects']}
        self.assertEqual(len(actual), 4089)
        for obj in legacy['objects']:
            self.assertEqual(actual[obj['id']]['discovery'], obj['discovery'])
        self.assertEqual(sum(bool(o['trajectory']) for o in directory['objects']), 14)
        for o in directory['objects']:
            if o['trajectory']:
                self.assertTrue(actual[o['id']]['discovery']['events'])
        for id in ['c1000109','c1000068']:
            event = actual[id]['discovery']['events'][0]
            self.assertEqual(event['certainty'], 'day-disputed')
            self.assertIsNone(event['date']['day'])
        self.assertIsNone(actual['c1003667']['discovery']['catalogue_date'])
        self.assertTrue(actual['c1003667']['discovery']['events'])

    def test_reject_source_overwrite_and_identity_mismatch(self):
        original = json.loads
        for mode in ['source','identity']:
            def altered(raw):
                v = original(raw)
                if mode == 'source' and isinstance(v,dict) and 'sources' in v and 'objects' in v and 'comet_contract_version' not in v:
                    v['sources']['halley'] = {'url':'https://invalid.example','checked_on':'2026-11-25'}
                if mode == 'identity' and isinstance(v,dict) and 'c1000068' in v:
                    v['c1000068']['response']['object']['spkid']='123'
                return v
            with patch.object(m.json,'loads',side_effect=altered):
                with self.assertRaises(ValueError):m.build()

    def test_candidate_rejects_production_path_escape(self):
        with self.assertRaises(ValueError):
            m.write_candidate(ROOT / '.context/../data/comets/unpublished.json')
        import tempfile
        with tempfile.TemporaryDirectory(dir=ROOT / '.context') as temporary:
            link = Path(temporary) / 'escape'
            link.symlink_to(ROOT / 'data', target_is_directory=True)
            with self.assertRaises(ValueError):
                m.write_candidate(link / 'comets/unpublished.json')

    def test_rejects_valid_different_directory_generation(self):
        import tempfile
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            directory = root / 'data/comets'
            directory.mkdir(parents=True)
            original = ROOT / 'data/comets'
            latest = json.loads((original / 'latest.json').read_bytes())
            value = json.loads((original / latest['index']['url']).read_bytes())
            value['snapshot']['retrieved_on'] = '2026-11-26'
            raw = m.encoded(value)
            reference = dict(url='index-'+m.sha(raw)+'.json',sha256=m.sha(raw),bytes=len(raw))
            (directory / reference['url']).write_bytes(raw)
            (directory / 'latest.json').write_bytes(m.encoded(dict(comet_contract_version=4,index=reference)))
            with patch.object(m,'ROOT',root):
                with self.assertRaisesRegex(ValueError,'reassess'):m.build()
