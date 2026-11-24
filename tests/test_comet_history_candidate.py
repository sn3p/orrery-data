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
            with self.subTest(mutate=mutate), patch.object(candidate.pilot, 'validate_metadata', return_value=value), self.assertRaises(DataError):
                candidate.construct()

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
            root=Path(temp);(root/'objects').mkdir()
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
                return b'{"signature":{"version":"1.2"}}',dict(url=url,sha256='a'*64,retrieved_at='2026-11-24T12:00:00Z'),(rows,'model')
            with patch.object(candidate.pilot,'acquire',side_effect=acquire):
                files=candidate.construct(root)
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
