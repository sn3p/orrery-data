"""Saved-release validation must pin its source dataset and run its checks."""

import hashlib
import json
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
        # Small valid inputs differ from the retained full dataset. Only the
        # preflight tests substitute fixture identities; optimization tests
        # keep production expectations and must reject before reading inputs.
        self.snapshot = self.cli("refresh", "--store", self.store,
                                 "--mpcorb", ROOT / "tests/fixtures/MPCORB.DAT",
                                 "--numbered", ROOT / "tests/fixtures/NumberedMPs.txt")
        for flags in ((), ("--limit", "100000")):
            self.cli("export", "--store", self.store, "--output", self.references, *flags)

    def cli(self, *args):
        result = subprocess.run([sys.executable, "-m", "orrery_data", *map(str, args)],
                                cwd=ROOT, capture_output=True, text=True, check=True, timeout=30)
        return json.loads(result.stdout)

    def preflight(self, work, report):
        # Substitute a small retained fixture and clean producer Git checkout.
        # Run the real argument/file/preflight path, stopping only at the costly
        # release-build boundary; no source-identity check is mocked.
        producer = self.directory / "producer"
        if not producer.exists():
            subprocess.run(["git", "init", "--quiet", str(producer)], check=True, capture_output=True)
            subprocess.run(["git", "-c", "user.name=Test", "-c", "user.email=test@example.invalid",
                            "-c", "commit.gpgsign=false", "commit", "--quiet", "--allow-empty", "-m", "Fixture"],
                           cwd=producer, check=True, capture_output=True)
        version = self.snapshot["snapshot_version"]
        master = self.store / "snapshots" / version / "master.jsonl.gz"
        script = ("import sys\nfrom pathlib import Path\n"
                  f"sys.path.insert(0, {str(ROOT / 'scripts')!r})\n"
                  "import validate_saved_release as validator\n"
                  f"validator.ROOT = Path({str(producer)!r})\n"
                  f"validator.EXPECTED_SNAPSHOT_VERSION = {version!r}\n"
                  f"validator.MASTER_SHA256 = {hashlib.sha256(master.read_bytes()).hexdigest()!r}\n"
                  "def stop(*args):\n"
                  " assert args[0] == 'prepare-release'\n"
                  " raise SystemExit('release preparation reached')\n"
                  "validator.cli = stop\nvalidator.main()\n")
        return subprocess.run([sys.executable, "-c", script, "--store", str(self.store),
                               "--reference-exports", str(self.references), "--work-dir", str(work),
                               "--report", str(report)], cwd=ROOT, env={**os.environ, "PYTHONOPTIMIZE": "0"},
                              capture_output=True, text=True, timeout=30)

    def test_changed_sources_with_identical_master_rejected_before_writes(self):
        dates = (ROOT / "tests/fixtures/NumberedMPs.txt").read_text()
        changed = self.directory / "changed-dates.txt"
        changed.write_text(dates + dates.splitlines(keepends=True)[0].replace("(1)", "(4)"))
        updated = self.cli("refresh", "--store", self.store,
                           "--mpcorb", ROOT / "tests/fixtures/MPCORB.DAT", "--numbered", changed)
        self.assertNotEqual(updated["snapshot_version"], self.snapshot["snapshot_version"])
        for key in ("master_records", "known_discovery", "missing_discovery"):
            self.assertEqual(updated["counts"][key], self.snapshot["counts"][key])
        for key in ("discovery_records", "unmatched_discovery_records"):
            self.assertEqual(updated["counts"][key], self.snapshot["counts"][key] + 1)
        references = [json.loads(p.read_text()) for p in self.references.glob("*/manifest.json")]
        for flags in ((), ("--limit", "100000")):
            exported = self.cli("export", "--store", self.store, "--output", self.references, *flags)
            exported = json.loads((Path(exported["path"]) / "manifest.json").read_text())
            reference = next(r for r in references if r["selection"] == exported["selection"])
            self.assertEqual(exported["artifacts"], reference["artifacts"])
        before = {str(p): p.read_bytes() for p in self.store.rglob("*") if p.is_file()}
        for existing in (False, True):
            with self.subTest(existing_report=existing):
                work, report = self.directory / f"work-{existing}", self.directory / f"report-{existing}.json"
                previous = b'{"previous_evidence":"keep"}\n'
                if existing:
                    report.write_bytes(previous)
                result = self.preflight(work, report)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("requires retained validated 2026-09-12 source snapshot", result.stderr)
                self.assertFalse(result.stdout)
                self.assertFalse(work.exists())
                if existing:
                    self.assertEqual(report.read_bytes(), previous)
                else:
                    self.assertFalse(report.exists())
        self.assertEqual({str(p): p.read_bytes() for p in self.store.rglob("*") if p.is_file()}, before)

    def test_retained_source_reaches_release_preparation(self):
        work, report = self.directory / "work", self.directory / "report.json"
        result = self.preflight(work, report)
        self.assertIn("release preparation reached", result.stderr)
        self.assertTrue(work.is_dir())
        self.assertFalse(report.exists())

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
