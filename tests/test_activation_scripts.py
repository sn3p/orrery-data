"""Activation command defaults and immutable scientific baseline boundaries."""
import gzip
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from scripts import comet_history_candidate as candidate
from scripts import rebuild_comet_evidence as evidence
from scripts.comet_baseline import ROOT, SCHEMA2_BASELINE, SCHEMA2_PIN, verify_baseline
from orrery_data import comets
from orrery_data.browser import bytes_reference
from orrery_data.formats import DataError
from orrery_data.paths import validate_writable_path
from orrery_data.storage import json_bytes


class ActivationScripts(unittest.TestCase):
    def test_default_candidate_uses_authenticated_baseline_before_raw_replay(self):
        self.assertEqual(verify_baseline()[0]['pin']['sha256'], SCHEMA2_PIN)
        with patch.object(candidate.pilot, 'acquire', side_effect=RuntimeError('raw replay reached')) as acquire:
            with self.assertRaisesRegex(RuntimeError, 'raw replay reached'):
                candidate.construct()
        acquire.assert_called_once()

    def test_rehashed_valid_baseline_change_rejected_before_replay(self):
        with tempfile.TemporaryDirectory() as temp:
            baseline = Path(temp) / 'site/comets'
            shutil.copytree(SCHEMA2_BASELINE, baseline)
            latest = json.loads((baseline / 'latest.json').read_bytes())
            index_file = baseline / latest['index']['url']
            index = json.loads(index_file.read_bytes())
            index['objects'].reverse()
            raw = json_bytes(index)
            ref = bytes_reference(raw, 'index-')
            index_file.unlink()
            (baseline / ref['url']).write_bytes(raw)
            (baseline / 'latest.json').write_bytes(json_bytes(dict(comet_contract_version=2,index=ref)))
            # This is a valid schema-2 product, but not the approved replay input.
            comets.verify_comets(baseline)
            with self.assertRaisesRegex(DataError, 'approved index pin'):
                verify_baseline(baseline)
            with patch.object(candidate, 'SCHEMA2_BASELINE', baseline), patch.object(candidate.pilot, 'acquire') as acquire:
                with self.assertRaisesRegex(DataError, 'approved index pin'):
                    candidate.construct(baseline)
                acquire.assert_not_called()

    def test_baseline_writers_and_aliases_fail_before_acquisition(self):
        before = (SCHEMA2_BASELINE / 'latest.json').read_bytes()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            alias = root / 'alias'; alias.symlink_to(SCHEMA2_BASELINE, target_is_directory=True)
            for directory in (SCHEMA2_BASELINE, alias):
                for output in (directory, directory / 'objects/new'):
                    with self.assertRaisesRegex(DataError, 'immutable frozen schema-2 baseline'):
                        validate_writable_path(output)
                with patch('orrery_data.horizons.acquire') as acquire:
                    with self.assertRaisesRegex(DataError, 'immutable frozen schema-2 baseline|must be a real directory'):
                        comets.refresh_comets(root / 'cache', directory)
                    acquire.assert_not_called()
            self.assertFalse((root / 'cache').exists())
        self.assertEqual((SCHEMA2_BASELINE / 'latest.json').read_bytes(), before)

    def test_production_refresh_has_release_specific_error_before_acquisition(self):
        with tempfile.TemporaryDirectory() as temp, patch('orrery_data.horizons.acquire') as acquire:
            with self.assertRaisesRegex(DataError, 'refresh-comets cannot refresh.*schema-3 historical release'):
                comets.refresh_comets(Path(temp) / 'cache', ROOT / 'data/comets')
            acquire.assert_not_called()
            self.assertEqual(list(Path(temp).iterdir()), [])

    def test_current_schema3_evidence_rebuild_uses_modern_segments(self):
        directory = ROOT / 'data/comets'
        latest = json.loads((directory / 'latest.json').read_bytes())
        index = json.loads((directory / latest['index']['url']).read_bytes())
        original = ROOT / 'tests/fixtures/comet-midpoints.json.gz'
        midpoints = json.loads(gzip.decompress(original.read_bytes()))
        rows = {}
        for obj in index['objects']:
            modern = next(segment for segment in obj['segments'] if segment['key'] == 'modern')
            series = json.loads((directory / modern['data']['url']).read_bytes())['series']
            rows[(obj['id'], False)] = [[series['start'] + i * series['step'], *state]
                                        for i, state in enumerate(series['states'])]
            rows[(obj['id'], True)] = midpoints[obj['id'] + '-midpoints']['rows']
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); output = root / 'evidence.gz'
            with patch.object(evidence, 'pinned_rows', side_effect=lambda cache, source, ident, midpoint, targets: rows[ident, midpoint]):
                result = evidence.rebuild(directory, root / 'cache', output)
            self.assertEqual(result['objects'], len(index['objects']))
            self.assertEqual(output.read_bytes(), original.read_bytes())
