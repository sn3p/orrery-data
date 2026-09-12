"""Saved-release validation must not report success with disabled assertions."""

import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class SavedReleaseValidation(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.store = self.directory / "store"
        self.references = self.directory / "references"
        # These small, valid producer inputs deliberately are NOT the required
        # retained full dataset. Without assertions the validator accepts them
        # and emits all its successful-validation flags anyway.
        self.cli("refresh", "--store", self.store,
                 "--mpcorb", ROOT / "tests/fixtures/MPCORB.DAT",
                 "--numbered", ROOT / "tests/fixtures/NumberedMPs.txt")
        for flags in ((), ("--limit", "100000")):
            self.cli("export", "--store", self.store, "--output", self.references, *flags)

    def cli(self, *args):
        subprocess.run([sys.executable, "-m", "orrery_data", *map(str, args)],
                       cwd=ROOT, capture_output=True, text=True, check=True, timeout=30)

    def reject_optimized(self, label, flags=(), optimization=None, existing_report=False):
        work = self.directory / label
        report = self.directory / (label + ".json")
        previous = b'{"previous_evidence":"must remain unchanged"}\n'
        if existing_report:
            report.write_bytes(previous)
        env = os.environ.copy()
        env.pop("PYTHONOPTIMIZE", None)
        if optimization is not None:
            env["PYTHONOPTIMIZE"] = optimization
        result = subprocess.run([sys.executable, *flags, str(ROOT / "scripts/validate_saved_release.py"),
                                 "--store", str(self.store), "--reference-exports", str(self.references),
                                 "--work-dir", str(work), "--report", str(report)],
                                cwd=ROOT, env=env, capture_output=True, text=True, timeout=30)
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn("requires assertions", result.stderr)
        self.assertFalse(result.stdout)
        self.assertFalse(work.exists(), "must reject before preparing artifacts")
        if existing_report:
            self.assertEqual(report.read_bytes(), previous)
        else:
            self.assertFalse(report.exists(), "must not write a passing report")

    def test_optimized_flags_reject_before_artifacts_or_reports(self):
        for flag in ("-O", "-OO"):
            with self.subTest(flag=flag):
                self.reject_optimized(flag[1:], flags=(flag,))

    def test_optimization_environment_preserves_existing_reports(self):
        for value in ("1", "2"):
            with self.subTest(value=value):
                self.reject_optimized("env-" + value, optimization=value, existing_report=True)

    def test_normal_execution_still_exposes_the_validator_cli(self):
        env = {**os.environ, "PYTHONOPTIMIZE": "0"}
        result = subprocess.run([sys.executable, str(ROOT / "scripts/validate_saved_release.py"), "--help"],
                                cwd=ROOT, env=env, capture_output=True, text=True, check=True, timeout=30)
        self.assertIn("--reference-exports", result.stdout)
        self.assertFalse(result.stderr)
