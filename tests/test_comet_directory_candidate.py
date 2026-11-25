import importlib.util
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch
import json

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('directory_candidate', ROOT / 'scripts/comet_directory_candidate.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


@unittest.skipUnless(shutil.which('node'), 'Candidate verification requires Node')
class DirectoryCandidateTests(unittest.TestCase):
    def test_exact_immutable_candidate_and_baseline_preservation(self):
        parent = ROOT / '.context/comet-candidates'
        parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=parent) as temporary:
            output = Path(temporary)
            output.rmdir()
            result = module.build(output)
            self.assertEqual(result['files'], 27)
            self.assertEqual(module.build(output), result)
            for file in (ROOT / 'data/comets').rglob('*.json'):
                if file.name != 'latest.json':
                    self.assertEqual((output / file.relative_to(ROOT / 'data/comets')).read_bytes(), file.read_bytes())
            (output / 'unexpected').write_text('retain')
            with self.assertRaisesRegex(ValueError, 'immutable'):
                module.build(output)
            self.assertEqual((output / 'unexpected').read_text(), 'retain')

    def test_public_output_and_alias_rejected(self):
        with self.assertRaisesRegex(ValueError, 'direct directory'):
            module.build(ROOT / 'data/comets')
        parent = ROOT / '.context/comet-candidates'
        with tempfile.TemporaryDirectory(dir=parent) as temporary:
            alias = Path(temporary) / 'alias'
            alias.symlink_to(ROOT / 'data/comets', target_is_directory=True)
            with self.assertRaises(ValueError):
                module.build(alias)

    def test_alias_introduced_during_verification_is_rejected(self):
        parent = ROOT / '.context/comet-candidates'
        with tempfile.TemporaryDirectory(dir=parent) as temporary:
            output = Path(temporary)
            output.rmdir()
            def race(argv, **kwargs):
                output.symlink_to(Path(argv[-1]), target_is_directory=True)
            with patch.object(module.subprocess, 'run', side_effect=race):
                with self.assertRaisesRegex(ValueError, 'changed during'):
                    module.build(output)
            output.unlink()
            output.mkdir()

    def test_reported_pin_must_match_descriptor(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture = Path(tmp)
            shutil.copytree(module.FIXTURE, fixture, dirs_exist_ok=True)
            spec = json.loads((fixture / 'fixture.json').read_bytes())
            spec['candidate']['sha256'] = '0' * 64
            (fixture / 'fixture.json').write_text(json.dumps(spec))
            with patch.object(module, 'FIXTURE', fixture):
                with self.assertRaisesRegex(ValueError, 'pin differs'):
                    module.build(ROOT / '.context/comet-candidates/invalid-pin-test')
