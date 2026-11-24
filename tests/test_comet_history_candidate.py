"""Candidate boundaries; no source acquisition, servers or public writes."""
import copy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts import comet_history_candidate as candidate
from orrery_data.formats import DataError


class HistoryCandidateTests(unittest.TestCase):
    def test_modern_snapshot_excludes_refresh_and_releases_lock(self):
        from orrery_data.storage import site_lock_path, writer_lock
        import shutil
        with tempfile.TemporaryDirectory() as temp:
            modern=Path(temp)/'site/comets'
            shutil.copytree(candidate.ROOT/'data/comets',modern)
            inspect=candidate.inspect_comets
            lock=site_lock_path(modern.parent)
            def inspect_during_refresh(*args, **kwargs):
                result=inspect(*args, **kwargs)
                with self.assertRaisesRegex(DataError,'Another writer'):
                    with writer_lock(lock): pass
                return result
            with patch.object(candidate,'inspect_comets',side_effect=inspect_during_refresh):
                index,files=candidate.snapshot_modern(modern)
            with writer_lock(lock):
                shutil.rmtree(modern)
            self.assertEqual(set(files),{e['data']['url'] for e in index['objects']})
            self.assertTrue(all(files.values()))

    def test_other_writers_cannot_change_installed_candidate(self):
        from orrery_data.horizons import acquire
        from orrery_data.paths import validate_writable_path
        from orrery_data.storage import writer_lock
        files={'latest.json':candidate.json_bytes({'index':{'sha256':'test'}})}
        with tempfile.TemporaryDirectory(dir=candidate.ROOT/'.context/comet-candidates') as temp:
            root=Path(temp);installed=root/'installed'
            with patch.object(candidate,'construct',return_value=files):candidate.build(installed)
            alias=root/'alias';alias.symlink_to(installed,target_is_directory=True)
            for base in (installed,alias):
                for path in (base,base/'objects',base/'new/deep'):
                    with self.subTest(path=path):
                        with self.assertRaisesRegex(DataError,'immutable'):
                            validate_writable_path(path)
                        with self.assertRaisesRegex(DataError,'immutable'):
                            acquire(path,offline=True,targets=['1P'])
                        with self.assertRaisesRegex(DataError,'immutable'):
                            with writer_lock(path): pass
                        candidate.verify(installed,files)
            # Site products remain refreshable; namespace staging stays writable.
            validate_writable_path(candidate.ROOT/'data/comets')
            validate_writable_path(root/'sibling')

    def test_redirected_namespace_and_retained_copies_are_immutable(self):
        import shutil
        from orrery_data.paths import validate_writable_path
        from orrery_data.horizons import acquire
        files={'latest.json':candidate.json_bytes({'comet_contract_version':3,'index':{'sha256':'test'}})}
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);project=root/'project';external=root/'external'
            (project/'.context').mkdir(parents=True);external.mkdir()
            namespace=project/'.context/comet-candidates';namespace.symlink_to(external,target_is_directory=True)
            with patch.object(candidate,'ROOT',project),patch.object(candidate,'construct',return_value=files):
                candidate.build(namespace/'installed')
            retained=root/'retained';shutil.copytree(external/'installed',retained)
            for base in (namespace/'installed',external/'installed',retained):
                for path in (base,base/'new/deep'):
                    with self.subTest(path=path):
                        with self.assertRaisesRegex(DataError,'immutable'):validate_writable_path(path)
                        with self.assertRaisesRegex(DataError,'immutable'):acquire(path,offline=True,targets=['1P'])
                        candidate.verify(base,files)

    def test_public_metadata_rejects_reader_incompatible_fields(self):
        original = candidate.pilot.validate_metadata(candidate.ROOT / 'research/comet-history')
        candidate.validate_metadata(original)
        mutations = [
            lambda o: o.update(extra=True),
            lambda o: o.update(event_inventory_complete=True),
            lambda o: o['events'][0].update(people='not-an-array'),
            lambda o: o['catalogue_provenance'].update(retrieved_at='unknown-time'),
            lambda o: o['events'][0].update(sources=['missing']),
            lambda o: o['events'][0].update(certainty='guess'),
        ]
        for mutate in mutations:
            value = copy.deepcopy(original)
            mutate(value['objects'][0])
            with self.subTest(mutate=mutate), self.assertRaises(DataError):
                candidate.validate_metadata(value)
            with patch.object(candidate.pilot, 'validate_metadata', return_value=value), patch.object(
                    candidate.pilot, 'acquire', side_effect=AssertionError('metadata reached acquisition')) as acquire:
                with self.assertRaisesRegex(DataError, 'metadata|Discovery'):
                    candidate.construct()
                acquire.assert_not_called()

    def test_public_metadata_semantics_do_not_depend_on_pilot_validation(self):
        original = candidate.pilot.validate_metadata(candidate.ROOT/'research/comet-history')
        mutations = [
            lambda o:o['events'][0]['date'].update(precision='invalid'),
            lambda o:o['events'][0]['date'].update(calendar='invalid'),
            lambda o:o['events'][0]['date'].update(year_numbering='civil'),
            lambda o:o['events'][0]['date'].update(month=99),
            lambda o:o['events'][0]['date'].update(precision='day',year=2026,month=2,day=30),
            lambda o:o['events'][0].update(kind='fiction'),
            lambda o:o['catalogue_provenance'].update(raw_file='../source'),
        ]
        for mutation in mutations:
            value=copy.deepcopy(original);mutation(value['objects'][0])
            with self.subTest(mutation=mutation),self.assertRaises(DataError):candidate.validate_metadata(value)
            with patch.object(candidate.pilot,'validate_metadata',return_value=value),self.assertRaises(DataError):candidate.construct()

    def test_shared_serialization_and_names_match_file_helpers(self):
        from orrery_data.storage import write_json
        from orrery_data.browser import reference
        value={'unicode':'é','nested':[1,None,True]}
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'value.json';write_json(path,value)
            self.assertEqual(candidate.json_bytes(value),path.read_bytes())
            for prefix in ['index-','objects/']:
                self.assertEqual(candidate.reference(path.read_bytes(),prefix),reference(path,prefix,'.json'))

    def test_empty_source_keys_rejected_even_when_unused(self):
        value=candidate.pilot.validate_metadata(candidate.ROOT/'research/comet-history')
        value['sources']['']=value['sources']['sbdb']
        with self.assertRaisesRegex(DataError,'metadata'):candidate.validate_metadata(value)

    def test_parser_retains_api_version_without_second_json_decode(self):
        from orrery_data import horizons
        raw=(candidate.ROOT/'tests/fixtures/horizons-1p.json').read_bytes().replace(b'2100-Jan-01',b'1980-Jan-09')
        with patch.object(horizons,'loads_json',wraps=horizons.loads_json) as decode:
            rows,source=candidate.pilot.parse_with_provenance(raw,'1P/Halley','JPL#75',2444239.5,3)
            self.assertEqual(len(rows),3);self.assertEqual(source['api_version'],'1.2')
            self.assertEqual(decode.call_count,1)

    def test_changed_historical_target_or_solution_rejected_before_acquisition(self):
        _, original = candidate.inspect_comets(candidate.ROOT/'data/comets', contract=candidate.CometContract(2))
        for field in ('name', 'solution'):
            index=copy.deepcopy(original)
            if field == 'name': index['objects'][0]['name']='1P/Renamed'
            else: index['objects'][0]['provenance']['vectors']['solution']='JPL#76'
            with patch.object(candidate,'inspect_comets',return_value=({},index)), patch.object(candidate.pilot,'acquire') as acquire:
                with self.assertRaisesRegex(DataError,'changed'):
                    candidate.construct()
                acquire.assert_not_called()

    def test_public_output_rejected_before_replay_or_writes(self):
        with patch.object(candidate, 'construct') as construct:
            for output in [candidate.ROOT/'data', candidate.ROOT/'data/comets', candidate.ROOT/'.context/comet-candidates']:
                with self.assertRaises(DataError):
                    candidate.build(output)
            construct.assert_not_called()
        with tempfile.TemporaryDirectory(dir=candidate.ROOT/'.context') as temp:
            alias = Path(temp)/'alias'
            alias.symlink_to(candidate.ROOT/'data', target_is_directory=True)
            with self.assertRaises(DataError):
                candidate.build(alias/'comets')

    def test_nested_outputs_reject_before_replay_and_preserve_original(self):
        files={'latest.json':candidate.json_bytes({'index':{'sha256':'test'}})}
        with tempfile.TemporaryDirectory(dir=candidate.ROOT/'.context/comet-candidates') as temp:
            root=Path(temp);first=root/'first'
            with patch.object(candidate,'construct',return_value=files):candidate.build(first)
            alias=root/'alias';alias.symlink_to(first,target_is_directory=True)
            def inventory():
                return {p.relative_to(first).as_posix():p.read_bytes() if p.is_file() else None
                        for p in first.rglob('*')}
            before=inventory()
            for output in [first/'nested',first/'deeper/nested',alias/'nested',alias/'deeper/nested']:
                with self.subTest(output=output),patch.object(candidate,'construct') as replay,patch.object(candidate,'writer_lock') as lock:
                    with self.assertRaisesRegex(DataError,'nested inside'):
                        candidate.build(output)
                    replay.assert_not_called();lock.assert_not_called()
                self.assertEqual(inventory(),before)
                candidate.verify(first,files)
            # Existing candidate verification remains supported; sibling builders
            # place neither locks nor staging files inside the first candidate.
            with patch.object(candidate,'construct',return_value=files):
                candidate.build(first);candidate.build(root/'sibling')
            self.assertEqual(inventory(),before)

    def test_ancestor_installed_during_replay_is_rechecked_under_namespace_lock(self):
        files={'latest.json':candidate.json_bytes({'index':{'sha256':'test'}})}
        with tempfile.TemporaryDirectory(dir=candidate.ROOT/'.context/comet-candidates') as temp:
            first=Path(temp)/'first';output=first/'deeper/nested'
            def replay():
                first.mkdir();(first/'latest.json').write_bytes(files['latest.json'])
                return files
            with patch.object(candidate,'construct',side_effect=replay):
                with self.assertRaisesRegex(DataError,'nested inside'):candidate.build(output)
            self.assertEqual(list(first.iterdir()),[first/'latest.json'])
            candidate.verify(first,files)

    def test_exact_verifier_rejects_corruption_missing_unknown_and_special_files(self):
        expected = {'latest.json': b'{}\n', 'objects/abc.json': b'[]\n'}
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp); (root/'objects').mkdir()
            for name, raw in expected.items(): (root/name).write_bytes(raw)
            # Descriptor isn't a schema fixture; validation equality happens before report.
            for bad in ['unknown', 'objects/abc.json']:
                (root/bad).write_bytes(b'wrong')
                with self.assertRaises(DataError): candidate.verify(root, expected)
                if bad in expected: (root/bad).write_bytes(expected[bad])
                else: (root/bad).unlink()
            pipe=root/'pipe';os.mkfifo(pipe)
            with self.assertRaises(DataError): candidate.verify(root,expected)
            pipe.unlink(); (root/'objects/abc.json').unlink()
            with self.assertRaises(DataError): candidate.verify(root,expected)

    def test_build_is_immutable_and_failure_preserves_existing_candidate(self):
        files={'latest.json': candidate.json_bytes({'index':{'sha256':'test'}})}
        with tempfile.TemporaryDirectory(dir=candidate.ROOT/'.context/comet-candidates') as temp:
            root=Path(temp)/'result'
            with patch.object(candidate,'construct',return_value=files):
                candidate.build(root);candidate.build(root,verify_only=True)
                self.assertEqual((root/'latest.json').read_bytes(),files['latest.json'])
            with patch.object(candidate,'construct',return_value={'latest.json':b'changed'}), self.assertRaises(DataError):
                candidate.build(root)
            self.assertEqual((root/'latest.json').read_bytes(),files['latest.json'])

    def test_construct_serializes_historical_grids_and_preserves_modern_bytes(self):
        # Production inspect/references run over a synthetic zero trajectory.
        # Acquisition is the only mocked boundary; real parsing lives in pilot tests.
        with tempfile.TemporaryDirectory() as temp:
            # Mirror the site/product hierarchy so its sibling publication lock
            # stays in this fixture on Linux as well as macOS.
            root=Path(temp)/'data/comets';(root/'objects').mkdir(parents=True)
            latest=json.loads((candidate.ROOT/'data/comets/latest.json').read_text())
            index=json.loads((candidate.ROOT/'data/comets'/latest['index']['url']).read_text())
            originals={}
            for e in index['objects']:
                value=json.loads((candidate.ROOT/'data/comets'/e['data']['url']).read_text())
                value['series']['states']=[[0]*6 for _ in value['series']['states']]
                raw=candidate.json_bytes(value);e['data']=candidate.reference(raw,'objects/')
                (root/e['data']['url']).write_bytes(raw);originals[e['data']['url']]=raw
                e['validation']['max_error_au']=0
                e['validation']['references']=[[r[0],0,0,0] for r in e['validation']['references']]
            raw=candidate.json_bytes(index);ref=candidate.reference(raw,'index-')
            (root/ref['url']).write_bytes(raw)
            (root/'latest.json').write_bytes(candidate.json_bytes({'comet_contract_version':2,'index':ref}))
            def acquire(url, online, validate):
                from urllib.parse import parse_qs,urlparse
                params=parse_qs(urlparse(url).query)
                first=float(params['START_TIME'][0].strip("'")[2:]);last=float(params['STOP_TIME'][0].strip("'")[2:])
                rows=[[first+4*i,0,0,0,0,0,0] for i in range(round((last-first)/4)+1)]
                return b'{"signature":{"version":"1.2"}}',dict(url=url,sha256='a'*64,retrieved_at='2026-11-24T12:00:00Z'),(rows,{'header':'model','api_version':'1.2'})
            # Refresh may prune every old payload once the snapshot lock is
            # released, including while historical replay is still running.
            validate_metadata=candidate.pilot.validate_metadata
            retired=root/'retired-objects'
            def prune_after_snapshot(*args):
                from orrery_data.storage import site_lock_path, writer_lock
                with writer_lock(site_lock_path(root.parent)):
                    (root/'objects').rename(retired)
                return validate_metadata(*args)
            writer_lock=candidate.writer_lock
            def confined_lock(path):
                self.assertTrue(path.resolve().is_relative_to(Path(temp).resolve()),
                                f'Test lock escaped temporary directory: {path}')
                return writer_lock(path)
            try:
                with patch.object(candidate.pilot,'acquire',side_effect=acquire), patch.object(
                        candidate.pilot,'validate_metadata',side_effect=prune_after_snapshot), patch.object(
                        candidate,'writer_lock',side_effect=confined_lock):
                    files=candidate.construct(root)
            finally:
                if retired.exists():retired.rename(root/'objects')
            for name,raw in originals.items(): self.assertEqual(files[name],raw)
            latest=json.loads(files['latest.json']);built=json.loads(files[latest['index']['url']])
            self.assertEqual(len(built['objects']),6)
            for e in built['objects']:
                if e['id'] not in ('1P','55P'): self.assertIsNone(e['transition']);continue
                old=e['segments'][0]
                self.assertFalse(old['coverage']['end_inclusive'])
                self.assertEqual(old['sample_coverage'][1],candidate.T+(e['id']=='55P'))
                self.assertEqual(e['transition']['owner'],'modern')
            # Perturbing modern velocity escapes position-only reference checks but
            # must fail the actual transition velocity check after acquisition.
            with patch.object(candidate.pilot,'acquire',side_effect=acquire), patch.object(candidate,'interpolate_state',side_effect=[([0]*3,[1]*3),([0]*3,[0]*3)]), self.assertRaisesRegex(DataError,'position/velocity'):
                candidate.construct(root)

    @classmethod
    def setUpClass(cls):
        (candidate.ROOT/'.context/comet-candidates').mkdir(parents=True,exist_ok=True)
