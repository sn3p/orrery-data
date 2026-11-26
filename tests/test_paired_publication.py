"""Offline production refresh, retention, verification and installation boundaries."""
import json
import subprocess
from contextlib import contextmanager
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch
from scripts import update_browser, named_body_candidate, prepare_browser_site
from orrery_data import paired
from orrery_data.browser import export_browser
from orrery_data.formats import DataError
from orrery_data.site import verify_site, copy_site_snapshot
from orrery_data.storage import read_json, file_info, write_json

ROOT=Path(__file__).resolve().parents[1]
class PairedPublication(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup);self.root=Path(temp.name).resolve()
        self.output=self.root/'data'; d=self.root/'research/named-bodies';d.mkdir(parents=True)
        shutil.copyfile(ROOT/'research/named-bodies/definitions.json',d/'definitions.json')
        for module in (update_browser,named_body_candidate):
            p=patch.object(module,'ROOT',self.root);p.start();self.addCleanup(p.stop)
    def update(self,day=1,science=None,published=None,fixture='ties'):
        if science is not None:
            p=self.root/'research/named-bodies/definitions.json';d=read_json(p)
            d['bodies'][0]['sources']['fit']['retrieved_at']=f'2026-11-{science:02}T00:00:00Z';write_json(p,d)
        with patch.object(paired,'now',return_value=f'2026-12-{day:02}T00:00:00Z'),patch.object(paired,'published_descriptor',return_value=published or {}):
            return update_browser.update_from_bundle(ROOT/'tests/fixtures/consumer-v1'/fixture,self.output)
    def tree(self):
        return {str(p.relative_to(self.output)):file_info(p) for p in self.output.rglob('*') if p.is_file()}
    def latest(self): return read_json(self.output/'paired/latest.json')
    def test_published_confirmation_grace_capacity_and_noop(self):
        self.update(science=1); self.update(2,science=2,published=self.latest())
        ledger=read_json(self.output/'paired/retention.json');self.assertIsNone(ledger['generations'][0]['superseded_at'])
        # Only a confirmed hosted replacement starts the older pair's clock.
        self.update(3,science=3,published=self.latest());self.update(4,science=4,published=self.latest())
        before=self.tree()
        with self.assertRaisesRegex(DataError,'retention capacity'):self.update(5,science=5,published=self.latest())
        self.assertEqual(before,self.tree())
        with self.assertRaisesRegex(DataError,'retention capacity'):self.update(20,science=5)
        self.assertEqual(before,self.tree())
        self.update(20,science=5,published=self.latest());verify_site(self.output)
        self.assertEqual(len(read_json(self.output/'paired/retention.json')['generations']),3)
        # First no-op may record a newly confirmed supersession; next is byte stable.
        self.update(21,science=5,published=self.latest());before=self.tree()
        self.assertEqual(self.update(22,science=5,published=self.latest())['status'],'unchanged')
        self.assertEqual(before,self.tree())
    def test_reselect_retained_pair_at_capacity(self):
        for n in range(1,5): self.update(n,science=n)
        first=read_json(self.output/'paired/retention.json')['generations'][0]['id']
        self.update(5,science=1)
        self.assertEqual(read_json(self.output/'paired/retention.json')['generations'][-1]['id'],first)
    def test_corruption_and_swap_failure_preserve_previous(self):
        self.update();before=self.tree()
        with patch.object(paired,'exchange_directories',side_effect=OSError('interrupted')):
            with self.assertRaisesRegex(OSError,'interrupted'):self.update(science=2)
        self.assertEqual(before,self.tree());verify_site(self.output)
        self.update(science=2);verify_site(self.output)
        ledger=read_json(self.output/'paired/retention.json')
        old=self.output/'paired/generations'/ledger['generations'][0]['id']
        target=next((old/'named').glob('mapping-*.json'));target.write_text('{}')
        with self.assertRaisesRegex(DataError,'Checksum'):verify_site(self.output)
        before=self.tree()
        with self.assertRaises(DataError):self.update(science=3)
        self.assertEqual(before,self.tree())
    def test_snapshot_mismatch_symlink_and_low_level_bypass(self):
        self.update()
        copy_site_snapshot(self.output,self.root/'copy');verify_site(self.root/'copy')
        with self.assertRaisesRegex(DataError,'Paired sites require'):
            export_browser(ROOT/'tests/fixtures/consumer-v1/empty',self.output)
        p=self.output/'paired/latest.json';d=read_json(p);d['asteroids']['sha256']='a'*64;write_json(p,d)
        with self.assertRaises(DataError):verify_site(self.output)
        p.unlink();p.symlink_to(self.root/'copy/paired/latest.json')
        with self.assertRaises(DataError):verify_site(self.output)
    def test_changed_asteroids_and_optional_absence(self):
        first=self.update();before=self.latest();second=self.update(2,fixture='empty')
        self.assertNotEqual(first['pin'],second['pin']);self.assertNotEqual(before,self.latest())
        self.assertTrue(all(m['status']=='absent-from-export' for m in second['named_bodies']['mapping']['members']))
        checked=verify_site(self.output)
        self.assertEqual(checked['files'],len(paired.owned_files(self.output)))
        self.assertEqual(set(second['changes']['added']+second['changes']['changed']+second['changes']['unchanged']),paired.owned_files(self.output))
    def test_science_only_remote_reconciliation(self):
        self.update();old=self.latest();self.update(science=2)
        def remote(url,optional=False):
            if url.endswith('paired/latest.json'):return old
            if url.endswith('paired/retention.json'):return read_json(self.output/'paired/retention.json')
            if url.endswith('comets/latest.json'):return None
            return read_json(self.output/'latest.json')
        with patch.object(prepare_browser_site,'remote_descriptor',side_effect=remote):
            self.assertFalse(prepare_browser_site.published_site_matches(self.output,paired.BASE+'latest.json'))

    def test_raw_export_rechecks_activation_under_lock(self):
        from orrery_data import browser
        self.output.mkdir()
        @contextmanager
        def lock(_):
            (self.output/'paired').mkdir()
            yield
        with patch.object(browser,'writer_lock',lock):
            with self.assertRaisesRegex(DataError,'Paired sites require'):
                export_browser(ROOT/'tests/fixtures/consumer-v1/empty',self.output)
        self.assertFalse((self.output/'latest.json').exists())

    def test_scheduled_validation_accepts_real_science_only_inventory(self):
        from scripts import prepare_scheduled_refresh as helper
        self.update()
        def git(*args):
            subprocess.run(['git', '-c','user.name=Test','-c','user.email=test@example.invalid',
                            '-c','commit.gpgsign=false','-c','core.hooksPath=/dev/null',*args],
                           cwd=self.root,check=True,capture_output=True)
        git('init','--quiet')
        (self.root/'.gitignore').write_text('.context/\nresearch/\n.*-browser-lock/\n')
        git('add','.gitignore','data');git('commit','--quiet','-m','initial pair')
        result=self.update(science=2)
        changes=helper.worktree_changes(self.root)
        helper.validate_results(self.root,result,verify_site(self.output),changes)
        self.assertIn('data/paired/latest.json',changes['paths'])
        self.assertNotIn('data/latest.json',changes['paths'])
