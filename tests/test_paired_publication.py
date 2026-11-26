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
from orrery_data import publication
from orrery_data.site import verify_site, verify_asteroids, copy_site_snapshot
from orrery_data.storage import read_json, file_info, write_json

ROOT=Path(__file__).resolve().parents[1]
class PairedPublication(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup);self.root=Path(temp.name).resolve()
        self.output=self.root/'data'; d=self.root/'research/named-bodies';d.mkdir(parents=True)
        shutil.copyfile(ROOT/'research/named-bodies/definitions.json',d/'definitions.json')
        for module in (update_browser,named_body_candidate):
            p=patch.object(module,'ROOT',self.root);p.start();self.addCleanup(p.stop)
    def update(self,day=1,science=None,published=None,fixture='ties',base_url=paired.BASE):
        if science is not None:
            p=self.root/'research/named-bodies/definitions.json';d=read_json(p)
            d['bodies'][0]['sources']['fit']['retrieved_at']=f'2026-11-{science:02}T00:00:00Z';write_json(p,d)
        with patch.object(paired,'now',return_value=f'2026-12-{day:02}T00:00:00Z'),patch.object(paired,'published_descriptor',return_value=published or {}):
            return update_browser.update_from_bundle(ROOT/'tests/fixtures/consumer-v1'/fixture,self.output,base_url=base_url)
    def tree(self):
        return {str(p.relative_to(self.output)):file_info(p) for p in self.output.rglob('*') if p.is_file()}
    def latest(self): return read_json(self.output/'paired/latest.json')
    def test_published_confirmation_grace_capacity_and_noop(self):
        self.update(science=1); self.update(2,science=2,published=self.latest())
        ledger=read_json(self.output/'paired/retention.json');self.assertIsNone(ledger['generations'][0]['superseded_at'])
        # Only a confirmed hosted replacement starts the older pair's clock.
        self.update(3,science=3,published=self.latest());self.update(4,science=4,published=self.latest())
        before=self.tree()
        old_pair=self.latest()
        result=self.update(5,science=5,published=self.latest())
        self.assertTrue(result['deferred']); self.assertEqual(self.latest(),old_pair)
        before=self.tree()
        self.assertTrue(self.update(20,science=5)['deferred'])
        self.assertEqual(before,self.tree())
        self.update(20,science=5,published=self.latest());verify_site(self.output)
        self.assertEqual(len(read_json(self.output/'paired/retention.json')['generations']),2)
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

    def test_refresh_preserves_comets_without_claiming_their_verification(self):
        from scripts.comet_baseline import SCHEMA2_BASELINE
        shutil.copytree(SCHEMA2_BASELINE,self.output/'comets')
        before={str(p.relative_to(self.output)):p.read_bytes() for p in (self.output/'comets').rglob('*') if p.is_file()}
        result=self.update()
        self.assertNotIn('comets',result)
        self.assertEqual(before,{p:(self.output/p).read_bytes() for p in before})
        self.assertEqual(verify_site(self.output)['comets']['path'],str(self.output/'comets'))

    def test_full_unstamped_ledger_commits_evidence_and_recovers_in_clone(self):
        from scripts import prepare_scheduled_refresh as helper
        for n in range(1,5): self.update(n,science=n)
        def git(*args):
            subprocess.run(['git','-c','user.name=Test','-c','user.email=test@example.invalid',
                            '-c','commit.gpgsign=false','-c','core.hooksPath=/dev/null',*args],
                           cwd=self.root,check=True,capture_output=True)
        git('init','--quiet');(self.root/'.gitignore').write_text('.context/\nresearch/\n.*-browser-lock/\nclone/\n')
        git('add','.gitignore','data');git('commit','-qm','four pairs')
        latest=self.latest();result=self.update(5,science=5,published=latest)
        self.assertEqual(result['status'],'updated');self.assertTrue(result['deferred'])
        self.assertEqual(self.latest(),latest)
        changes=helper.worktree_changes(self.root)
        helper.validate_results(self.root,result,verify_site(self.output),changes)
        self.assertEqual(changes['paths'],{'data/paired/retention.json'})
        git('add','data');git('commit','-qm','confirm retirement')
        git('clone','--quiet',str(self.root),str(self.root/'clone'))
        self.output=self.root/'clone/data'
        self.assertIsNone(self.update(20,science=5,published=self.latest())['deferred'])
        self.assertNotEqual(self.latest(),latest)

    def test_size_missing_file_and_budget_boundaries(self):
        from orrery_data import site
        self.update();p=self.output/'paired/retention.json';raw=p.read_bytes()
        p.write_bytes(raw+b' '*(4096-len(raw)));verify_site(self.output)
        copy_site_snapshot(self.output,self.root/'copy')
        p.write_bytes(p.read_bytes()+b' ')
        with self.assertRaises(DataError):verify_site(self.output)
        with self.assertRaises(DataError):copy_site_snapshot(self.output,self.root/'oversize')
        p.unlink()
        with self.assertRaises(DataError):verify_site(self.output)
        p.write_bytes(raw);before=self.tree()
        with patch.object(site,'MAX_SITE_BYTES',1):
            with self.assertRaisesRegex(DataError,'byte budget'):self.update(science=2)
        self.assertEqual(before,self.tree())
        generation=read_json(p)['generations'][0]['id']
        (self.output/'paired/generations'/generation/'pair.json').unlink()
        with self.assertRaises(DataError):verify_site(self.output)

    def test_fork_base_and_migration(self):
        self.update();old=self.latest()
        base='https://fork.example.test/project/'
        result=self.update(base_url=base)
        self.assertEqual(old['asteroids']['sha256'],self.latest()['asteroids']['sha256'])
        self.assertTrue(self.latest()['asteroids']['url'].startswith(base));verify_site(self.output)
        import io
        with patch.object(publication,'urlopen',return_value=io.BytesIO(json.dumps(self.latest()).encode())) as request:
            self.assertEqual(paired.published_descriptor(base),self.latest())
            self.assertEqual(request.call_args.args[0],base+'paired/latest.json')
        before=self.tree()
        with self.assertRaises(DataError):self.update(base_url='https://user:password@example.test/')
        self.assertEqual(before,self.tree())

    def test_base_migration_resets_old_host_retirement_clocks(self):
        for n in range(1,5):self.update(n,science=n)
        self.update(5,science=5,published=self.latest())
        base='https://fork.example.test/new/'
        self.update(20,science=4,base_url=base)
        ledger=read_json(self.output/'paired/retention.json')
        self.assertTrue(all(e['superseded_at'] is None for e in ledger['generations']))
        result=self.update(21,science=5,base_url=base,published=self.latest())
        self.assertTrue(result['deferred'])
        self.assertEqual(len(read_json(self.output/'paired/retention.json')['generations']),4)

    def test_remote_metadata_reconciliation_enforces_4096_bytes(self):
        import io
        raw=b'{}'+b' '*4094
        with patch.object(publication,'urlopen',return_value=io.BytesIO(raw)):
            self.assertEqual(prepare_browser_site.remote_descriptor('https://example.test/retention.json'),{})
        with patch.object(publication,'urlopen',return_value=io.BytesIO(raw+b' ')):
            with self.assertRaisesRegex(ValueError,'Oversized'):
                prepare_browser_site.remote_descriptor('https://example.test/retention.json')

    def test_cli_configured_base_and_invalid_port(self):
        import sys
        target=self.root/'cli-site'
        command=[sys.executable,str(ROOT/'scripts/update_browser.py'),'--from-bundle',
                 str(ROOT/'tests/fixtures/consumer-v1/ties'),'--output',str(target),'--base-url']
        bad=subprocess.run([*command,'https://example.test:invalid/'],capture_output=True,text=True)
        self.assertNotEqual(bad.returncode,0);self.assertFalse(target.exists())
        good=subprocess.run([*command,'https://fork.example.test/repository/'],capture_output=True,text=True)
        self.assertEqual(good.returncode,0,good.stderr)
        self.assertTrue(read_json(target/'paired/latest.json')['asteroids']['url'].startswith('https://fork.example.test/repository/'))
        verify_site(target)

    def test_full_ledger_without_confirmation_keeps_root_fresh_and_pair_intact(self):
        for n in range(1,5): self.update(n,science=n)
        latest=self.latest()
        retained={str(p.relative_to(self.output)):p.read_bytes() for p in (self.output/'paired').rglob('*') if p.is_file()}
        old_root=read_json(self.output/'latest.json')
        for hosted in (None,{'stale':True}):
            with self.subTest(hosted=hosted):
                result=self.update(6,science=5,fixture='empty',published=hosted)
                self.assertTrue(result['deferred'])
                self.assertNotEqual(old_root,read_json(self.output/'latest.json'))
                self.assertEqual(self.latest(),latest)
                self.assertEqual(retained,{p:(self.output/p).read_bytes() for p in retained})
                verify_site(self.output)
        self.update(7,science=5,published=latest)
        self.assertIsNone(self.update(22,science=5,fixture='empty',published=latest)['deferred'])
        self.assertEqual(self.latest()['asteroids']['sha256'],read_json(self.output/'latest.json')['index']['sha256'])

    def test_science_pairs_share_one_asteroid_catalogue(self):
        first=self.update(science=1)
        url=self.latest()['asteroids']['url']
        for n in range(2,5):
            self.update(n,science=n)
            self.assertEqual(self.latest()['asteroids']['url'],url)
        self.assertEqual(len(list((self.output/'paired/asteroids').iterdir())),1)
        self.assertFalse(any(p.name=='asteroids' for p in (self.output/'paired/generations').rglob('*')))
        self.assertLess(verify_site(self.output)['bytes']-first['bytes'],100_000)

    def test_comet_corruption_does_not_block_authoring_but_blocks_pages(self):
        from scripts.comet_baseline import SCHEMA2_BASELINE
        shutil.copytree(SCHEMA2_BASELINE,self.output/'comets')
        self.update()
        orphan=self.output/'comets/objects'/('0'*64+'.json');orphan.write_text('{}')
        comets={str(p.relative_to(self.output)):p.read_bytes() for p in (self.output/'comets').rglob('*') if p.is_file()}
        previous=self.latest()
        result=self.update(2,fixture='empty')
        self.assertNotEqual(self.latest(),previous)
        self.assertEqual(result['pin'],verify_asteroids(self.output)['pin'])
        self.assertEqual(comets,{p:(self.output/p).read_bytes() for p in comets})
        with self.assertRaisesRegex(DataError,'Comet inventory differs'):verify_site(self.output)
        import sys
        attempt=subprocess.run([sys.executable,str(ROOT/'scripts/prepare_browser_site.py'),
                                '--source',str(self.output),'--output',str(self.root/'pages')],capture_output=True,text=True)
        self.assertNotEqual(attempt.returncode,0);self.assertFalse((self.root/'pages').exists())
        self.assertIn('Comet inventory differs',attempt.stderr)

    def test_noop_hardlinks_payloads_and_verifies_retained_catalogue_once_outside_lock(self):
        from orrery_data.storage import writer_lock, site_lock_path
        for n in range(1,5):self.update(n,science=n)
        verify=paired.verify_browser
        visits=[]
        def checking(directory,**kwargs):
            with writer_lock(site_lock_path(self.output)):pass
            old=self.output/'paired/asteroids'/directory.name
            chunk=next((old/'chunks').iterdir())
            self.assertEqual(chunk.stat().st_ino,(directory/'chunks'/chunk.name).stat().st_ino)
            visits.append(directory)
            return verify(directory,**kwargs)
        with patch.object(paired,'verify_browser',side_effect=checking):
            self.assertEqual(self.update(5,science=4)['status'],'unchanged')
        self.assertEqual(len(visits),1)

    def test_concurrent_comet_writer_is_preserved_and_refresh_can_retry(self):
        from orrery_data.storage import writer_lock, site_lock_path
        self.update()
        previous=self.latest()
        build=paired.build_candidate
        def racing(*args,**kwargs):
            with writer_lock(site_lock_path(self.output)):
                (self.output/'comets').mkdir()
                (self.output/'comets/new-object.json').write_text('concurrent writer')
            return build(*args,**kwargs)
        with patch.object(paired,'build_candidate',side_effect=racing):
            with self.assertRaisesRegex(DataError,'site changed during refresh'):self.update(2,fixture='empty')
        self.assertEqual(self.latest(),previous)
        self.assertEqual((self.output/'comets/new-object.json').read_text(),'concurrent writer')
        self.update(2,fixture='empty')
        self.assertNotEqual(self.latest(),previous)
        self.assertEqual((self.output/'comets/new-object.json').read_text(),'concurrent writer')

    def test_pages_http_and_unavailable_configuration_at_cli_boundary(self):
        import sys
        command=[sys.executable,str(ROOT/'scripts/update_browser.py'),'--from-bundle',
                 str(ROOT/'tests/fixtures/consumer-v1/ties'),'--pages-base-url']
        for base,paired_expected in [('http://example.test/project/',True),('',False)]:
            with self.subTest(base=base):
                target=self.root/('http-site' if base else 'unconfigured-site')
                result=subprocess.run([*command,base,'--output',str(target)],capture_output=True,text=True)
                self.assertEqual(result.returncode,0,result.stderr)
                verify_asteroids(target)
                self.assertEqual((target/'paired/latest.json').exists(),paired_expected)
                if paired_expected:
                    self.assertTrue(read_json(target/'paired/latest.json')['asteroids']['url'].startswith('https://example.test/project/'))
                else:self.assertTrue(json.loads(result.stdout)['deferred'])
        self.update();latest=self.latest()
        result=self.update(2,fixture='empty',base_url=None)
        self.assertTrue(result['deferred']);self.assertEqual(self.latest(),latest)
        self.assertNotEqual(result['pin']['sha256'],latest['asteroids']['sha256'])
        verify_site(self.output)

    def test_expiry_removes_only_unreferenced_shared_catalogues(self):
        for day in range(1,5):
            self.update(day,science=day,fixture='ties' if day%2 else 'empty')
        self.assertEqual(len(list((self.output/'paired/asteroids').iterdir())),2)
        self.update(5,science=5,fixture='empty',published=self.latest())
        self.update(20,science=5,fixture='empty',published=self.latest())
        checked=verify_site(self.output)
        self.assertEqual(checked['paired']['generations'],2)
        self.assertEqual([p.name for p in (self.output/'paired/asteroids').iterdir()],[self.latest()['asteroids']['sha256']])
