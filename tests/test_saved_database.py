"""The saved-data validator must regenerate exports even in a reused work directory."""

from pathlib import Path
import json
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from scripts import validate_saved_database as validator


ROOT = Path(__file__).resolve().parents[1]


class SavedDatabaseValidation(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.work = Path(self.temp.name)
        self.store = self.work / "store"
        self.references = self.work / "references"
        snapshot, _ = validator.cli("refresh", "--store", self.store,
                                    "--mpcorb", ROOT / "tests/fixtures/MPCORB.DAT",
                                    "--numbered", ROOT / "tests/fixtures/NumberedMPs.txt")
        self.version = snapshot["snapshot_version"]
        for flags in ((), ("--limit", 100000)):
            validator.cli("export", "--store", self.store, "--output", self.references, *flags)
        # Populate the old fixed path with valid artifacts to reproduce a rerun.
        for flags in ((), ("--limit", 100000)):
            validator.cli("export", "--store", self.store, "--output", self.work / "exports", *flags)

    def verify(self):
        return validator.verify_exports(self.store, self.version, self.references, self.work, {})

    def test_reruns_generate_both_profiles_in_distinct_retained_directories(self):
        first, first_path = self.verify()
        second, second_path = self.verify()
        self.assertNotEqual(first_path, self.work / "exports")
        self.assertNotEqual(first_path, second_path)
        self.assertEqual(first, second)
        for directory in (first_path, second_path):
            self.assertEqual(len(list(directory.glob("*/manifest.json"))), 2)
            self.assertEqual(len(list(directory.glob("*/catalog.json.gz"))), 2)
        self.assertEqual(len(list((self.work / "exports").glob("*/manifest.json"))), 2)

    def test_cached_exports_cannot_hide_serialization_failure(self):
        self.verify()  # A prior successful validation must not mask a new failure.
        run = subprocess.run
        fault = '''from unittest.mock import patch
from orrery_data.cli import main
with patch("orrery_data.pipeline.deterministic_gzip", side_effect=OSError("injected serialization failure")):
    raise SystemExit(main())
'''

        def fail_new_serialization(command, **kwargs):
            self.assertEqual(command[1:4], ["-m", "orrery_data", "export"])
            return run([command[0], "-c", fault, *command[3:]], **kwargs)

        # Exercise the actual export CLI, including its cache-reuse branch.
        with patch.object(validator.subprocess, "run", side_effect=fail_new_serialization):
            with self.assertRaisesRegex(RuntimeError, "injected serialization failure"):
                self.verify()

    def test_committed_report_identifies_the_fresh_export_directory(self):
        report = json.loads((ROOT / "docs/sqlite-validation-result.json").read_text())
        self.assertEqual(report["status"], "passed")
        self.assertTrue(report["export_directory"].startswith("exports-"))
        self.assertEqual(Path(report["export_directory"]).name, report["export_directory"])


if __name__ == "__main__":
    unittest.main()
