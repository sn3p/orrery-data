"""Real offline refresh boundaries: matching published mapping before installation."""
import copy
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch
import yaml
from scripts import update_browser, named_body_candidate
from orrery_data import paired
from orrery_data.formats import DataError
from orrery_data.storage import read_json, file_info
from orrery_data.site import verify_site

ROOT=Path(__file__).resolve().parents[1]

class NamedBodyRefresh(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup);self.root=Path(temp.name).resolve()
        directory=self.root/'research/named-bodies';directory.mkdir(parents=True)
        shutil.copyfile(ROOT/'research/named-bodies/definitions.json',directory/'definitions.json')
        self.output=self.root/'data'
        for module in (update_browser,named_body_candidate):
            patcher=patch.object(module,'ROOT',self.root);patcher.start();self.addCleanup(patcher.stop)

    def update(self,fixture='ties'):
        with patch.object(paired, 'published_descriptor', return_value={}):
            return update_browser.update_from_bundle(ROOT/'tests/fixtures/consumer-v1'/fixture,self.output)

    def tree(self):
        return {str(p.relative_to(self.output)):p.read_bytes() for p in self.output.rglob('*') if p.is_file()}

    def test_refresh_changed_noop_and_science_update_publish_pairs(self):
        first=self.update();old=self.tree();candidate=Path(first['named_bodies']['path'])
        self.assertEqual(first['named_bodies']['activation'],'paired')
        self.assertEqual(first['pin']['sha256'],first['named_bodies']['mapping']['asteroid_index_sha256'])
        again=self.update();self.assertEqual(again['status'],'unchanged');self.assertEqual(again['named_bodies'],first['named_bodies'])
        self.assertEqual(old,self.tree());verify_site(self.output)
        self.assertTrue((self.output/'paired/latest.json').is_file())
        # Definitions-only refresh still prepares a new companion for the same
        # asteroid pin, without overwriting earlier reviewed candidate bytes.
        definition=self.root/'research/named-bodies/definitions.json';d=read_json(definition)
        d['bodies'][0]['sources']['fit']['retrieved_at']='2026-11-27T00:00:00Z'
        definition.write_text(json.dumps(d))
        revised=self.update();self.assertNotEqual(revised['named_bodies']['path'],str(candidate));self.assertNotEqual(self.tree(),old);self.assertEqual(revised['pin'],first['pin'])
        empty=self.update('empty');self.assertNotEqual(empty['pin'],first['pin'])
        self.assertEqual(empty['named_bodies']['mapping']['records'],0)
        self.assertTrue(all(m['status']=='absent-from-export' for m in empty['named_bodies']['mapping']['members']))
        self.assertTrue(candidate.is_dir());verify_site(self.output)

    def test_mapping_failure_prevents_asteroid_install_and_preserves_previous(self):
        first=self.update();old=self.tree();previous=file_info(Path(first['named_bodies']['path'])/'index.json')
        with patch.object(update_browser,'build_named_candidate',side_effect=DataError('mapping failed')):
            with self.assertRaisesRegex(DataError,'mapping failed'):self.update('empty')
        self.assertEqual(old,self.tree())
        self.assertEqual(file_info(Path(first['named_bodies']['path'])/'index.json'),previous)

    def test_scheduled_evidence_and_verified_site_are_published(self):
        workflow=yaml.load((ROOT/'.github/workflows/mpc-refresh.yml').read_text(),Loader=yaml.BaseLoader)
        steps=workflow['jobs']['refresh']['steps'];names=[s.get('name') for s in steps]
        upload=next(s for s in steps if s.get('name')=='Retain named-body generation evidence')
        self.assertIn('actions/upload-artifact@',upload['uses'])
        self.assertIn('${{ github.run_attempt }}',upload['with']['name'])
        self.assertIn('.context/named-body-candidates/refresh-*/',upload['with']['path'])
        self.assertEqual(upload['with']['if-no-files-found'],'error')
        self.assertEqual(upload['with']['include-hidden-files'],'true')
        self.assertLess(names.index(upload['name']),names.index('Commit and push changed browser data'))
        commit=next(s for s in steps if s.get('name')=='Commit and push changed browser data')
        self.assertIn('git add -A -- data',commit['run']);self.assertNotIn('named-body-candidates',commit['run'])
