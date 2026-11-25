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
            with patch.object(candidate.pilot, 'acquire') as acquire:
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
                    with self.assertRaisesRegex(DataError, 'immutable'):
                        validate_writable_path(output)
                with patch('orrery_data.horizons.acquire') as acquire:
                    with self.assertRaisesRegex(DataError, 'immutable|must be a real directory'):
                        comets.refresh_comets(root / 'cache', directory)
                    acquire.assert_not_called()
            self.assertFalse((root / 'cache').exists())
        self.assertEqual((SCHEMA2_BASELINE / 'latest.json').read_bytes(), before)

    def test_frozen_snapshot_uses_no_writer_lock_and_authenticates_captured_bytes(self):
        with patch.object(candidate, 'writer_lock', side_effect=AssertionError('immutable reader lock')):
            candidate.snapshot_modern(SCHEMA2_BASELINE)
            alias = SCHEMA2_BASELINE.with_name(SCHEMA2_BASELINE.name.upper())
            if alias.exists():
                candidate.snapshot_modern(alias)
        original = Path.read_bytes
        def changed(path):
            raw = original(path)
            return raw + b' ' if path.parent.name == 'objects' else raw
        inspect = candidate.inspect_comets
        def mutate_after_inspection(*args, **kwargs):
            result = inspect(*args, **kwargs)
            mutation.start()
            return result
        mutation = patch.object(Path, 'read_bytes', changed)
        try:
            with patch.object(candidate, 'inspect_comets', side_effect=mutate_after_inspection):
                with self.assertRaisesRegex(DataError, 'changed during snapshot'):
                    candidate.snapshot_modern(SCHEMA2_BASELINE)
        finally:
            mutation.stop()

    def test_corrupt_descriptor_rejected_before_acquisition(self):
        with tempfile.TemporaryDirectory() as temp, patch('orrery_data.horizons.acquire') as acquire:
            root = Path(temp); output = root / 'site/comets'; output.mkdir(parents=True)
            (output / 'latest.json').write_text('{broken')
            with self.assertRaises(ValueError):
                comets.refresh_comets(root / 'cache', output)
            acquire.assert_not_called()
            self.assertFalse((root / 'cache').exists())

    def test_production_refresh_has_release_specific_error_before_acquisition(self):
        with tempfile.TemporaryDirectory() as temp, patch('orrery_data.horizons.acquire') as acquire:
            with self.assertRaisesRegex(DataError, 'refresh-comets cannot refresh.*schema-[34] release'):
                comets.refresh_comets(Path(temp) / 'cache', ROOT / 'data/comets')
            acquire.assert_not_called()
            self.assertEqual(list(Path(temp).iterdir()), [])

    def test_current_schema3_evidence_rebuild_uses_modern_segments(self):
        directory = ROOT / 'data/comets'
        # Expected inputs come from independently retained schema-2 bytes, not
        # from the production extractor under test or a duplicate projection.
        _, baseline = verify_baseline()
        original = ROOT / 'tests/fixtures/comet-midpoints.json.gz'
        midpoints = json.loads(gzip.decompress(original.read_bytes()))
        rows, sources = {}, {}
        for obj in baseline['objects']:
            series = json.loads((SCHEMA2_BASELINE / obj['data']['url']).read_bytes())['series']
            rows[(obj['id'], False)] = [[series['start'] + i * series['step'], *state]
                                        for i, state in enumerate(series['states'])]
            rows[(obj['id'], True)] = midpoints[obj['id'] + '-midpoints']['rows']
            sources[(obj['id'], False)] = obj['provenance']['vectors']
            sources[(obj['id'], True)] = midpoints[obj['id'] + '-midpoints']['source']
        def pinned(cache, source, ident, midpoint, targets):
            self.assertEqual(source, sources[ident, midpoint])
            return rows[ident, midpoint]
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); output = root / 'evidence.gz'
            with patch.object(evidence, 'pinned_rows', side_effect=pinned):
                result = evidence.rebuild(directory, root / 'cache', output)
            self.assertEqual(result['objects'], len(baseline['objects']))
            self.assertEqual(output.read_bytes(), original.read_bytes())

    def test_evidence_missing_or_duplicate_modern_segment_has_clean_error(self):
        for segments in ([], [{'key': 'historical'}], [{'key': 'modern'}, {'key': 'modern'}]):
            index = {'comet_contract_version': 3, 'objects': [
                {'id': '1P', 'name': '1P/Halley', 'segments': segments}]}
            with self.subTest(segments=segments), self.assertRaisesRegex(DataError, '1P: expected exactly one modern segment'):
                evidence.modern_entries(index)
