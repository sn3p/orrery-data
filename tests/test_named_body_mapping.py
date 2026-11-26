from pathlib import Path
import shutil
import subprocess
import unittest
import copy
import json
import tempfile
from unittest.mock import patch
import uuid
from orrery_data.formats import DataError
from orrery_data.named_bodies import IDS
from orrery_data.named_body_mapping import build_mapping, validate_mapping
from orrery_data.pipeline import select_discovery_rows
from scripts.named_body_candidate import build

ROOT=Path(__file__).resolve().parents[1]

class NamedBodyMapping(unittest.TestCase):
    def test_selection_limit_precedes_sort_and_ties_keep_identity(self):
        rows=[dict(id=IDS[8],disc=20),dict(id=IDS[9],disc=10),dict(id=IDS[10],disc=10),dict(id=IDS[11],disc=None)]
        selected=select_discovery_rows(rows,2)
        self.assertEqual([r['id'] for r in selected],[IDS[9],IDS[8]])
        self.assertEqual([r['id'] for r in select_discovery_rows(rows,None)],[IDS[9],IDS[10],IDS[8]])


    def test_real_fixture_candidate_roundtrip_noop_and_corruption(self):
        export=ROOT/'tests/fixtures/consumer-v1/ties/full';browser=ROOT/'tests/fixtures/browser-v1/ties'
        mapping=build_mapping(export,browser)
        self.assertEqual(mapping['members'][0]['ordinal'],1)
        self.assertTrue(all(m['status']=='absent-from-export' for m in mapping['members'][1:]))
        output=ROOT/'.context/named-body-candidates'/('test-'+uuid.uuid4().hex)
        self.addCleanup(lambda:shutil.rmtree(output,ignore_errors=True))
        result=build(export,browser,output)
        before={p.name:p.read_bytes() for p in output.iterdir()}
        self.assertEqual(result,build(export,browser,output))
        subprocess.run(['node',str(ROOT/'scripts/verify_named_bodies.mjs'),str(output),str(browser)],check=True,capture_output=True)
        self.assertEqual(before,{p.name:p.read_bytes() for p in output.iterdir()})
        (output/'index.json').write_text('{}')
        with self.assertRaises(DataError):build(export,browser,output)
        with self.assertRaises(DataError):build(export,browser,ROOT/'data/named-bodies')


    def test_real_export_limit_and_missing_date_mapping(self):
        import tempfile
        from orrery_data.pipeline import refresh, export
        from orrery_data.storage import URLS
        from orrery_data.indexed import export_indexed
        from orrery_data.browser import export_browser
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp); fixture=ROOT/'tests/fixtures'
            # Reorder the actual MPC source so Ceres is after another known-date
            # record. Limiting must happen before discovery sorting.
            text=(fixture/'MPCORB.DAT').read_text();lines=text.splitlines(keepends=True)
            first=next(i for i,line in enumerate(lines) if line.startswith('00001'))
            lines[first],lines[first+1]=lines[first+1],lines[first]
            (root/'MPCORB.DAT').write_text(''.join(lines))
            refresh(root/'store',URLS,{'mpcorb':root/'MPCORB.DAT','numbered':fixture/'NumberedMPs.txt'},{},60)
            e=export(root/'store',root/'exports',limit=1)
            bundle=export_indexed(Path(e['path']),root/'indexed')
            export_browser(Path(bundle['path']),root/'browser')
            m=build_mapping(Path(e['path']),root/'browser')['members'][0]
            self.assertEqual((m['status'],m['reason'],m['ordinal']),('absent-from-export','selection-limit',None))
            self.assertIsNotNone(m['discovery_jd'])
            # A missing discovery date has a distinct absence reason, never a
            # fabricated row or a failed join reported as absence.
            dates=(fixture/'NumberedMPs.txt').read_text().splitlines(keepends=True)
            (root/'dates').write_text(''.join(line for line in dates if int(line[:8].strip()[1:-1])!=1))
            refresh(root/'other-store',URLS,{'mpcorb':fixture/'MPCORB.DAT','numbered':root/'dates'},{},60)
            e=export(root/'other-store',root/'other-exports')
            bundle=export_indexed(Path(e['path']),root/'other-indexed')
            export_browser(Path(bundle['path']),root/'other-browser')
            m=build_mapping(Path(e['path']),root/'other-browser')['members'][0]
            self.assertEqual((m['status'],m['reason'],m['discovery_jd']),('absent-from-export','unknown-discovery-date',None))


    def test_invalid_mapping_is_rejected_before_candidate_installation(self):
        export=ROOT/'tests/fixtures/consumer-v1/ties/full';browser=ROOT/'tests/fixtures/browser-v1/ties'
        good=build_mapping(export,browser)
        output=ROOT/'.context/named-body-candidates'/('test-'+uuid.uuid4().hex)
        for mutation in [lambda m:m['members'][0].update(ordinal=True),
                         lambda m:m['members'][1].update(status='unknown'),
                         lambda m:m.update(records=-1)]:
            bad=copy.deepcopy(good);mutation(bad)
            with self.assertRaises(DataError):validate_mapping(bad)
            with patch('scripts.named_body_candidate.build_mapping',return_value=bad),self.assertRaises(DataError):build(export,browser,output)
            self.assertFalse(output.exists())

    def test_captured_index_is_not_reopened_and_chunks_are_read_once(self):
        import orrery_data.browser as module
        import orrery_data.indexed as indexed
        import orrery_data.pipeline as pipeline
        export=ROOT/'tests/fixtures/consumer-v1/ties/full';browser=ROOT/'tests/fixtures/browser-v1/ties'
        with patch.object(module,'read_verified_json',wraps=module.read_verified_json) as reads, \
             patch.object(pipeline,'iter_master',wraps=pipeline.iter_master) as master:
            build_mapping(export,browser)
        self.assertEqual(master.call_count,1)
        paths=[c.args[0] for c in reads.call_args_list]
        self.assertEqual(len(paths),len(set(paths)))
        self.assertEqual(sum(p.name.startswith('index-') for p in paths),1)
        # Corrupt the index file after the verified bytes were captured. Mapping
        # must use the captured object, not the subsequently replaced contents.
        with tempfile.TemporaryDirectory() as temp:
            local=Path(temp)/'browser';shutil.copytree(browser,local)
            real=module.read_verified_json
            def replace_after_read(path,ref):
                value=real(path,ref)
                if path.name.startswith('index-'):path.write_text('{}')
                return value
            with patch.object(module,'read_verified_json',side_effect=replace_after_read):
                actual=build_mapping(export,local)
            self.assertEqual(actual['members'][0]['ordinal'],1)
            with self.assertRaises(DataError):build_mapping(export,local)

    def test_duplicate_identity_after_limit_is_rejected_in_shared_selection(self):
        import orrery_data.pipeline as pipeline
        from orrery_data.records import iter_master
        export=ROOT/'tests/fixtures/consumer-v1/ties/full'
        rows=list(iter_master(export/'master.jsonl.gz'))
        counts=json.loads((export/'manifest.json').read_text())['counts']
        with patch.object(pipeline,'iter_master',return_value=iter(rows+rows[:1])):
            with self.assertRaisesRegex(DataError,'Duplicate master identity'):
                pipeline.selected_master(export/'master.jsonl.gz',counts,1,identity_ids=set(IDS[8:]))

    def test_mismatched_browser_generation_rejected(self):
        with self.assertRaises(DataError):build_mapping(ROOT/'tests/fixtures/consumer-v1/ties/full',ROOT/'tests/fixtures/browser-v1/empty')
