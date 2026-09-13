"""Producer and cached-artifact validation at public API and CLI boundaries."""

import copy
import gzip
import json
from pathlib import Path
import subprocess
import sys
import unittest

import test_cli
from orrery_data.formats import DataError
from orrery_data.pipeline import refresh
from orrery_data.storage import URLS, file_info, read_json, write_json


class ExportActivation(unittest.TestCase):
    setUpClass = classmethod(test_cli.CLI.setUpClass.__func__)
    tearDownClass = classmethod(test_cli.CLI.tearDownClass.__func__)
    setUp = test_cli.CLI.setUp
    tree = test_cli.CLI.tree
    http_args = test_cli.CLI.http_args

    def cli(self, command, *args, code=0, script=None):
        invocation = [sys.executable, "-B", "-m", "orrery_data"]
        if script:
            invocation = [sys.executable, "-B", "-c", script]
        result = subprocess.run([*invocation, command, *map(str, args)], cwd=test_cli.ROOT,
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, code, result.stdout + result.stderr)
        self.assertFalse(result.stderr if code == 0 else result.stdout)
        return json.loads(result.stdout if code == 0 else result.stderr)

    def local_args(self, orbits=None):
        orbit_file, date_file = self.directory / "MPCORB.DAT", self.directory / "NumberedMPs.txt"
        orbit_file.write_text(self.orbits if orbits is None else orbits)
        date_file.write_text(self.dates)
        return ["--mpcorb", orbit_file, "--numbered", date_file]

    def export(self, *args, **options):
        return self.cli("export", "--store", self.store, "--output", self.output, *args, **options)

    def reseal_export(self, directory):
        manifest = read_json(directory / "manifest.json")
        for name in manifest["artifacts"]:
            manifest["artifacts"][name].update(file_info(directory / name))
        write_json(directory / "manifest.json", manifest)
        (directory / "SHA256SUMS").write_text("".join(
            f"{file_info(directory / name)['sha256']}  {name}\n"
            for name in sorted([*manifest["artifacts"], "manifest.json"])))

    def test_cached_boolean_catalog_cannot_replace_previous_pointer_and_can_retry(self):
        orbits = test_cli.change_field(self.orbits, "00001", 26, 35, "0.00000")
        self.cli("refresh", "--store", self.store, *self.local_args(orbits))
        full = self.export()
        target = Path(full["path"])
        original = {path.name: path.read_bytes() for path in target.iterdir()}
        previous = self.export("--limit", 2)
        for value in (False, True):
            with self.subTest(value=value):
                # Both booleans violate the numeric schema, including False,
                # which Python otherwise considers equal to the source 0.0.
                rows = json.loads(original["catalog.json"])
                rows[0]["M"] = value
                write_json(target / "catalog.json", rows)
                (target / "catalog.json.gz").write_bytes(gzip.compress((target / "catalog.json").read_bytes(), mtime=0))
                self.reseal_export(target)
                before = self.tree(self.output)
                self.assertIn("Invalid discovery catalog fields", self.export(code=1)["error"])
                self.assertEqual(self.tree(self.output), before)
                self.assertEqual(read_json(self.output / "latest.json"), {"data_version": previous["data_version"]})
        for name, payload in original.items():
            (target / name).write_bytes(payload)
        self.assertEqual(self.export(), full)
        self.assertEqual(read_json(self.output / "latest.json"), {"data_version": full["data_version"]})
        self.assertEqual(type(read_json(target / "catalog.json")[0]["M"]), float)

    def test_cached_and_fresh_exports_reconcile_actual_master_counts(self):
        refreshed = self.cli("refresh", "--store", self.store, *self.local_args())
        full = self.export()
        target = Path(full["path"])
        previous = self.export("--limit", 2)
        snapshot_path = Path(refreshed["path"]) / "snapshot.json"
        snapshot = read_json(snapshot_path)
        manifest = read_json(target / "manifest.json")
        original_files = {path.name: path.read_bytes() for path in target.iterdir()}
        for case in ("missing", "known"):
            with self.subTest(case=case):
                changed = copy.deepcopy(snapshot)
                fields = ("orbital_records", "master_records", "missing_discovery", "unnumbered_orbits") if case == "missing" else (
                    "known_discovery", "discovery_records", "numbered_orbits")
                for key in fields:
                    changed["counts"][key] += 1
                if case == "known":
                    changed["counts"]["missing_discovery"] -= 1
                    changed["counts"]["unnumbered_orbits"] -= 1
                changed["exclusions"]["discovery"]["missing_discovery_date"] = changed["counts"]["missing_discovery"]
                write_json(snapshot_path, changed)
                exported = copy.deepcopy(manifest)
                exported["counts"].update(changed["counts"])
                exported["counts"]["discovery_export"] = changed["counts"]["known_discovery"]
                exported["exclusions"].update(changed["exclusions"])
                exported["artifacts"]["master.jsonl.gz"]["records"] = changed["counts"]["master_records"]
                for name in ("catalog.json", "catalog.json.gz"):
                    exported["artifacts"][name]["records"] = changed["counts"]["known_discovery"]
                write_json(target / "manifest.json", exported)
                self.reseal_export(target)
                before = self.tree(self.output)
                self.assertIn("Master contents do not match snapshot counts", self.export(code=1)["error"])
                self.assertEqual(self.tree(self.output), before)
                self.assertEqual(read_json(self.output / "latest.json"), {"data_version": previous["data_version"]})
                fresh = self.directory / ("fresh-" + case)
                result = self.cli("export", "--store", self.store, "--output", fresh, code=1)
                self.assertIn("Master contents do not match snapshot counts", result["error"])
                self.assertFalse((fresh / "latest.json").exists())
                self.assertFalse(list(fresh.glob("export-v1-*")))
        write_json(snapshot_path, snapshot)
        for name, payload in original_files.items():
            (target / name).write_bytes(payload)
        self.assertEqual(self.export(), full)

    def test_refresh_invalid_urls_preserve_store_and_corrected_retry(self):
        args = self.local_args()
        self.assertIn("HTTP(S) URL", self.cli("refresh", "--store", self.store, *args,
                                              "--mpcorb-url", "not-a-url", code=1)["error"])
        self.assertFalse(self.store.exists())
        first = self.cli("refresh", "--store", self.store, *args)
        before = self.tree(self.store)
        changed = self.local_args(test_cli.change_field(self.orbits, "00001", 26, 35, "12.00000"))
        self.assertIn("HTTP(S) URL", self.cli("refresh", "--store", self.store, *changed,
                                              "--numbered-url", "file:///tmp/dates", code=1)["error"])
        self.assertEqual(self.tree(self.store), before)
        second = self.cli("refresh", "--store", self.store, *changed)
        self.assertNotEqual(first["snapshot_version"], second["snapshot_version"])
        self.assertEqual(read_json(self.store / "current.json"), {"snapshot_version": second["snapshot_version"]})

    def test_refresh_public_api_rejects_options_before_output(self):
        local = {"mpcorb": test_cli.FIXTURES / "MPCORB.DAT", "numbered": test_cli.FIXTURES / "NumberedMPs.txt"}
        defaults = {"urls": URLS, "local": local, "metadata": {}, "timeout": 60, "allow_count_decrease": False}
        invalid = [("urls", {}), ("urls", {**URLS, "mpcorb": "invalid"}), ("local", []),
                   ("local", {"mpcorb": local["mpcorb"]}), ("local", {**local, "numbered": False}),
                   ("local", {**local, "numbered": ""}), ("metadata", {"mpcorb": {"etag": "invalid"}}),
                   ("timeout", True), ("timeout", 0), ("timeout", 1.0), ("allow_count_decrease", 1)]
        for key, value in invalid:
            with self.subTest(key=key, value=value):
                with self.assertRaises(DataError):
                    refresh(self.store, **{**defaults, key: value})
                self.assertFalse(self.store.exists())
        result = refresh(self.store, **defaults)
        self.assertEqual(read_json(self.store / "current.json"), {"snapshot_version": result["snapshot_version"]})

    def test_new_snapshot_manifest_validation_preserves_prior_pointer(self):
        self.cli("refresh", "--store", self.store, *self.local_args())
        before = self.tree(self.store)
        changed = self.local_args(test_cli.change_field(self.orbits, "00001", 26, 35, "12.00000"))
        script = ("import orrery_data.pipeline as p; p.now=lambda: 'invalid'; "
                  "from orrery_data.cli import main; raise SystemExit(main())")
        result = self.cli("refresh", "--store", self.store, *changed, code=1, script=script)
        self.assertIn("timestamp", result["error"])
        self.assertEqual(self.tree(self.store), before)
        self.assertFalse(list(self.store.glob(".refresh-*")))
        result = self.cli("refresh", "--store", self.store, *changed)
        self.assertEqual(read_json(self.store / "current.json"), {"snapshot_version": result["snapshot_version"]})

    def test_new_export_manifest_validation_preserves_prior_pointer(self):
        self.cli("refresh", "--store", self.store, *self.local_args())
        self.export()
        before = self.tree(self.output)
        script = ("import orrery_data.pipeline as p; p.now=lambda: 'invalid'; "
                  "from orrery_data.cli import main; raise SystemExit(main())")
        self.assertIn("timestamp", self.export("--limit", 2, code=1, script=script)["error"])
        self.assertEqual(self.tree(self.output), before)
        self.assertFalse(list(self.output.glob(".export-*")))
        result = self.export("--limit", 2)
        self.assertEqual(read_json(self.output / "latest.json"), {"data_version": result["data_version"]})
