"""Regressions for reviewed catalogue contracts and refresh coordination."""
import io
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from orrery_data import comet_product, comets, comets_v2, horizons
from orrery_data.comet_product import CometContract, descriptor_bytes, valid_id, valid_name
from orrery_data.formats import DataError
from orrery_data.storage import writer_lock, read_json, site_lock_path

ROOT = Path(__file__).resolve().parents[1]

class ReviewRegressions(unittest.TestCase):
    def test_object_validation_enforces_version_selection(self):
        root = ROOT / 'data/comets-v2'
        index = read_json(root / read_json(root / 'latest.json')['index']['url'])
        for entry in index['objects']:
            value = read_json(root / entry['data']['url'])
            comets_v2.validate_object(value, entry['id'])
            value['comet_contract_version'] = 1
            if entry['id'] in comets.TARGETS:
                comets.validate_object(value, entry['id'])
            else:
                with self.assertRaisesRegex(DataError, 'identity'):
                    comets.validate_object(value, entry['id'])

    def test_refresh_verifies_old_and_candidate_outside_site_lock(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve(); output = root / 'data/comets-v2'
            shutil.copytree(ROOT / 'data/comets-v2', output)
            original_verify = comet_product.verify_comets
            checked = []
            def verify(directory, **kwargs):
                with writer_lock(site_lock_path(output.parent)):
                    checked.append(Path(directory))
                    return original_verify(directory, **kwargs)
            def build(records, stage, previous):
                shutil.copytree(output, stage, dirs_exist_ok=True)
                return verify(stage, contract=comets_v2.CONTRACT)
            with patch.object(comets_v2.PRODUCT, 'acquire', return_value={}), \
                 patch.object(comets_v2.PRODUCT, 'verify_comets', side_effect=lambda p, **kw: verify(p, contract=comets_v2.CONTRACT, **kw)), \
                 patch.object(comets_v2.PRODUCT, 'build_candidate', side_effect=build):
                result = comets_v2.refresh_comets(root / 'cache', output, offline=True)
            self.assertEqual(len(checked), 2)
            self.assertEqual(checked[0], output)
            self.assertEqual(result['status'], 'unchanged')
            self.assertEqual({k: result[k] for k in ('path', 'pin', 'objects', 'files', 'bytes')}, original_verify(output, contract=comets_v2.CONTRACT))

    def test_descriptor_snapshot_is_bounded(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary)/'latest.json'
            self.assertIsNone(descriptor_bytes(path))
            path.write_bytes(b'x'*4097)
            with self.assertRaisesRegex(DataError,'Oversized'):
                descriptor_bytes(path)

    def test_selection_diagnostic_identifies_actual_contract(self):
        with self.assertRaisesRegex(DataError, 'Comet v2 requires fixed selection: 9P'):
            CometContract(2, {'9P': '9P/Tempel 1'}).validate_selection([])

    def test_horizons_targets_are_required_before_io(self):
        with patch.object(horizons, 'download') as download:
            with self.assertRaises(TypeError): horizons.acquire('unused')
            with self.assertRaises(TypeError): horizons.parse_response(b'', '1P')
            download.assert_not_called()

    def test_common_identity_fixture(self):
        for case in json.loads((ROOT/'tests/fixtures/comet-identities.json').read_text()):
            with self.subTest(case=case):
                self.assertEqual(valid_id(case['id']) and valid_name(case['name'], case['id']), case['valid'])

    def test_both_versions_exclude_other_acquisition_even_with_different_caches(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for first, second in [(comets, comets_v2), (comets_v2, comets)]:
                def acquiring(*args, **kwargs):
                    # The publication lock remains free while acquiring.
                    with writer_lock(root / '.data-browser-lock'): pass
                    with patch.object(second.PRODUCT, 'acquire') as competing:
                        with self.assertRaises(DataError):
                            second.refresh_comets(root/'other-cache',root/f'data/v{second.CONTRACT.version}')
                        competing.assert_not_called()
                    raise DataError('stop after exclusion check')
                with patch.object(first.PRODUCT, 'acquire', side_effect=acquiring):
                    with self.assertRaisesRegex(DataError, 'stop after exclusion'):
                        first.refresh_comets(root/'cache',root/f'data/v{first.CONTRACT.version}')

    def test_candidate_releases_site_lock_and_rejects_changed_pointer(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); output = root/'data/comets-v2'
            shutil.copytree(ROOT/'data/comets-v2',output)
            original = (output/'latest.json').read_bytes()
            def build(records, stage, previous):
                with writer_lock(root/'.data-browser-lock'):
                    shutil.copytree(output, stage, dirs_exist_ok=True)
                    (output/'latest.json').write_bytes(b'changed by another writer')
            with patch.object(comets_v2.PRODUCT,'acquire',return_value={}), patch.object(comets_v2.PRODUCT,'build_candidate',side_effect=build):
                with self.assertRaisesRegex(DataError,'descriptor changed'):
                    comets_v2.refresh_comets(root/'cache',output,offline=True)
            self.assertEqual((output/'latest.json').read_bytes(),b'changed by another writer')
            (output/'latest.json').write_bytes(original)
            comets_v2.verify_comets(output)
