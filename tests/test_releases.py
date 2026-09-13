"""Release CLI and manual-workflow boundaries, including portable consumption."""

import fcntl
import gzip
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import unittest

import test_cli
from orrery_data.storage import file_info

ROOT = Path(__file__).resolve().parents[1]
COMMIT = "1" * 40


class ReleaseCLI(unittest.TestCase):
    setUpClass = classmethod(test_cli.CLI.setUpClass.__func__)
    tearDownClass = classmethod(test_cli.CLI.tearDownClass.__func__)
    setUp = test_cli.CLI.setUp
    tree = test_cli.CLI.tree
    http_args = test_cli.CLI.http_args

    def cli(self, command, *args, code=0, script=None):
        invocation = [sys.executable, "-m", "orrery_data"]
        if script:
            invocation = [sys.executable, "-c", script]
        result = subprocess.run([*invocation, command, *map(str, args)], cwd=ROOT,
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, code, result.stdout + result.stderr)
        self.assertFalse(result.stderr if code == 0 else result.stdout)
        return json.loads(result.stdout if code == 0 else result.stderr) if code != 2 else result.stderr

    def prepare(self, *args, code=0, script=None, offline=None):
        source = ["--snapshot", offline] if offline else self.http_args()
        return self.cli("prepare-release", "--store", self.store, "--output", self.output,
                        "--producer-commit", COMMIT, "--selected-limit", 2, *source, *args, code=code, script=script)

    def verify(self, path, *args, code=0):
        return self.cli("verify-release", "--bundle", path, *args, code=code)

    def test_local_sources_portable_bundle_fields_and_provenance(self):
        mpcorb, numbered = self.directory / "orbits", self.directory / "dates"
        mpcorb.write_text(self.orbits)
        numbered.write_text(self.dates)
        prepared = self.cli("prepare-release", "--store", self.store, "--output", self.output,
                            "--producer-commit", COMMIT, "--mpcorb", mpcorb, "--numbered", numbered,
                            "--selected-limit", 2, "--selected-limit", 4)
        bundle = Path(prepared["path"])
        copied = self.directory / "downloaded ?#% bundle"
        shutil.copytree(bundle, copied)
        before = self.tree(copied)
        shutil.rmtree(self.store)
        self.verify(copied, "--manifest-sha256", prepared["manifest"]["sha256"])
        rows = self.cli("query", "--database", copied / "orrery.sqlite3")["records"]
        with gzip.open(copied / "exports/full/master.jsonl.gz", "rt") as stream:
            self.assertEqual(rows, [json.loads(line) for line in stream])
        self.assertEqual(sum(row["disc"] is None for row in rows), 3)
        self.assertNotEqual(rows[0]["W"], rows[0]["w"])
        for profile, limit in (("full", 6), ("first-2", 2), ("first-4", 4)):
            catalog = json.loads((copied / f"exports/{profile}/catalog.json").read_text())
            expected = [{k: r[k] for k in test_cli.FIELDS} for r in rows if r["disc"] is not None][:limit]
            self.assertEqual(catalog, sorted(expected, key=lambda r: r["disc"]))
        manifest = json.loads((copied / "release.json").read_text())
        self.assertEqual(manifest["identity"]["producer"]["commit"], COMMIT)
        self.assertIsNone(manifest["sources"]["mpcorb"]["retrieved_at"])
        self.assertNotIn(str(self.directory), (copied / "release.json").read_text())
        self.assertIn("Minor Planet Center", " ".join((copied / "NOTICE.txt").read_text().split()))
        self.assertEqual(self.tree(copied), before)

    def test_repeat_pinned_preparation_and_separate_code_data_versions(self):
        first = self.prepare()
        before = self.tree(self.output)
        self.requests.clear()
        self.assertEqual(self.prepare(offline=first["snapshot_version"]), first)
        self.assertEqual(self.requests, [])
        self.assertEqual(self.tree(self.output), before)
        changed_code = self.prepare("--producer-commit", "2" * 40, offline=first["snapshot_version"])
        self.assertNotEqual(first["release_version"], changed_code["release_version"])
        self.assertEqual(first["dataset_version"], changed_code["dataset_version"])
        for profile in ("full", "first-2"):
            self.assertEqual(file_info(Path(first["path"]) / f"exports/{profile}/catalog.json"),
                             file_info(Path(changed_code["path"]) / f"exports/{profile}/catalog.json"))

    def test_limits_larger_than_sqlite_integer_select_all_eligible_records(self):
        limit = 2 ** 63
        prepared = self.prepare("--selected-limit", limit)
        bundle = Path(prepared["path"])
        full = bundle / "exports/full/catalog.json"
        selected = bundle / f"exports/first-{limit}/catalog.json"
        self.assertEqual(selected.read_bytes(), full.read_bytes())
        self.assertEqual(prepared["profiles"][f"first-{limit}"]["selection"]["limit"], limit)
        self.verify(bundle, "--manifest-sha256", prepared["manifest"]["sha256"])
        self.assertEqual(self.prepare("--selected-limit", limit, offline=prepared["snapshot_version"]), prepared)

    def test_dataset_version_independent_of_snapshot_tool_version(self):
        first = self.prepare()
        old = self.cli("refresh", "--store", self.directory / "old-store", *self.http_args(),
                       script="import orrery_data; orrery_data.__version__='0.2.0'; "
                              "from orrery_data.cli import main; raise SystemExit(main())")
        self.assertNotEqual(old["snapshot_version"], first["snapshot_version"])
        other = self.cli("prepare-release", "--store", self.directory / "old-store", "--output", self.output,
                         "--snapshot", old["snapshot_version"], "--producer-commit", COMMIT)
        self.assertEqual(first["dataset_version"], other["dataset_version"])
        self.assertNotEqual(first["release_version"], other["release_version"])

    def test_updated_removed_records_count_decrease_and_recovery(self):
        first = self.prepare()
        before = self.tree(self.output)
        changed = test_cli.change_field(self.orbits, "00001", 37, 46, "123.0")
        changed = "".join(line for line in changed.splitlines(keepends=True) if not line.startswith("00002"))
        body = gzip.compress(changed.encode(), mtime=0)
        self.responses["/orbits"]["body"] = body
        self.responses["/orbits"]["headers"]["Content-Length"] = str(len(body))
        self.assertIn("decreased", self.prepare(code=1)["error"])
        self.assertEqual(self.tree(self.output), before)
        second = self.prepare("--allow-count-decrease")
        self.assertNotEqual(first["dataset_version"], second["dataset_version"])
        database = Path(second["path"]) / "orrery.sqlite3"
        self.assertEqual(self.cli("query", "--database", database, "--number", 2)["records"], [])
        self.assertEqual(self.cli("query", "--database", database, "--number", 1)["records"][0]["w"], 123.0)
        self.verify(first["path"])
        self.assertEqual(self.prepare(offline=first["snapshot_version"])["release_version"], first["release_version"])
        self.prepare(offline=second["snapshot_version"], code=1)
        self.prepare("--allow-count-decrease", offline=second["snapshot_version"])

    def test_failures_preserve_previous_bundle_then_retry(self):
        first = self.prepare()
        before = self.tree(self.output)
        patches = [
            "patch('orrery_data.releases.build_database', side_effect=OSError('build failed'))",
            "patch('orrery_data.releases.export', side_effect=OSError('export failed'))",
            "patch('orrery_data.releases.verify_release', fail_verify)",
            "patch.object(Path, 'rename', fail_rename)",
        ]
        for patch in patches:
            with self.subTest(patch=patch):
                script = ("from unittest.mock import patch\nfrom pathlib import Path\n"
                          "from orrery_data.cli import main\n"
                          "original=Path.rename\nfrom orrery_data.releases import verify_release\n"
                          "def fail_verify(path, *args):\n"
                          " result=verify_release(path, *args)\n"
                          " if path.name.startswith('.release-'): raise OSError('verify failed')\n"
                          " return result\n"
                          "def fail_rename(self, target):\n"
                          " if self.name.startswith('.release-'): raise OSError('install failed')\n"
                          " return original(self, target)\n"
                          f"with {patch}: raise SystemExit(main())\n")
                self.prepare("--producer-commit", "2" * 40, offline=first["snapshot_version"], code=1, script=script)
                self.assertEqual(self.tree(self.output), before)
                self.assertFalse(list(self.output.glob(".release-*")))
        # Failure after installation leaves a verified orphan, never a partial latest.
        script = ("from unittest.mock import patch\nfrom orrery_data.cli import main\n"
                  "with patch('orrery_data.releases.atomic_json', side_effect=OSError('pointer failed')): "
                  "raise SystemExit(main())")
        self.prepare("--producer-commit", "2" * 40, offline=first["snapshot_version"], code=1, script=script)
        self.assertEqual(json.loads((self.output / "latest.json").read_text())["release_version"], first["release_version"])
        second = self.prepare("--producer-commit", "2" * 40, offline=first["snapshot_version"])
        self.verify(second["path"])

    def test_killed_preparation_retains_old_bundle_and_recovers(self):
        first = self.prepare()
        before = self.tree(self.output)
        signal = self.directory / "inserted"
        script = ("from unittest.mock import patch\nfrom pathlib import Path\nimport time\n"
                  "from orrery_data.cli import main\nfrom orrery_data.releases import build_database\n"
                  "def pause(*args, **kwargs):\n"
                  " result=build_database(*args, **kwargs)\n"
                  f" Path({str(signal)!r}).touch()\n time.sleep(30)\n return result\n"
                  "with patch('orrery_data.releases.build_database', pause): raise SystemExit(main())")
        process = subprocess.Popen([sys.executable, "-c", script, "prepare-release", "--store", str(self.store),
                                    "--output", str(self.output), "--snapshot", first["snapshot_version"],
                                    "--producer-commit", "2" * 40], cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            deadline = time.monotonic() + 10
            while not signal.exists() and time.monotonic() < deadline:
                time.sleep(0.05)
            self.assertTrue(signal.exists())
        finally:
            process.kill()
            process.communicate(timeout=10)
        self.assertEqual(file_info(Path(first["path"]) / "release.json")["sha256"], first["manifest"]["sha256"])
        self.assertEqual(self.tree(Path(first["path"])), {k.split('/', 1)[1]: v for k, v in before.items()
                                                         if k.startswith(Path(first["path"]).name + '/')})
        self.verify(self.prepare("--producer-commit", "2" * 40, offline=first["snapshot_version"])["path"])

    def test_damaged_missing_extra_symlink_and_manifest_files_rejected(self):
        first = self.prepare()
        for name in ("orrery.sqlite3", "exports/full/catalog.json", "exports/first-2/catalog.json.gz",
                     "exports/full/master.jsonl.gz", "snapshot.json", "NOTICE.txt", "release.json", "SHA256SUMS"):
            with self.subTest(name=name):
                copied = self.directory / "copy"
                shutil.copytree(first["path"], copied)
                with (copied / name).open("ab") as stream:
                    stream.write(b"damage")
                self.verify(copied, code=1)
                shutil.rmtree(copied)
        bundle = Path(first["path"])
        extra = bundle / "extra"
        extra.write_text("unexpected")
        self.verify(bundle, code=1)
        self.prepare(offline=first["snapshot_version"], code=1)
        extra.unlink()
        notice = bundle / "NOTICE.txt"
        data = notice.read_bytes()
        notice.unlink()
        self.verify(bundle, code=1)
        notice.symlink_to(ROOT / "orrery_data/NOTICE.txt")
        self.verify(bundle, code=1)
        notice.unlink()
        notice.write_bytes(data)
        self.verify(bundle, "--manifest-sha256", "0" * 64, code=1)
        self.verify(bundle)

    def reseal(self, bundle):
        # Recompute transport hashes to exercise semantic consistency checks below them.
        manifest = json.loads((bundle / "release.json").read_text())
        for name in manifest["artifacts"]:
            manifest["artifacts"][name] = file_info(bundle / name)
        (bundle / "release.json").write_text(json.dumps(manifest))
        names = sorted([*manifest["artifacts"], "release.json"])
        (bundle / "SHA256SUMS").write_text("".join(f"{file_info(bundle / n)['sha256']}  {n}\n" for n in names))

    def test_mixed_versions_counts_provenance_and_unsafe_paths_rejected(self):
        first = self.prepare()
        for mutation in ("snapshot", "counts", "profile", "paths", "notice"):
            with self.subTest(mutation=mutation):
                copied = self.directory / mutation
                shutil.copytree(first["path"], copied)
                manifest = json.loads((copied / "release.json").read_text())
                if mutation == "snapshot":
                    snapshot = json.loads((copied / "snapshot.json").read_text())
                    snapshot["sources"]["mpcorb"]["retrieved_at"] = "2020-01-01T00:00:00Z"
                    (copied / "snapshot.json").write_text(json.dumps(snapshot))
                elif mutation == "counts":
                    manifest["counts"]["known_discovery"] -= 1
                elif mutation == "profile":
                    manifest["profiles"]["first-2"] = manifest["profiles"]["full"]
                elif mutation == "paths":
                    manifest["artifacts"]["../outside"] = manifest["artifacts"].pop("NOTICE.txt")
                else:
                    (copied / "NOTICE.txt").write_text("Altered attribution")
                (copied / "release.json").write_text(json.dumps(manifest))
                if mutation != "paths":
                    self.reseal(copied)
                self.verify(copied, code=1)

    def test_bad_source_provenance_arguments_baseline_and_read_only_failures(self):
        baseline = self.directory / "baseline.json"
        baseline.write_text('{"orbital_records":100}')
        self.prepare("--baseline-counts", baseline, code=1)
        self.assertFalse(self.output.exists())
        self.prepare("--producer-commit", "master", code=1)
        self.assertFalse(self.output.exists())
        metadata = self.directory / "metadata.json"
        metadata.write_text('{"mpcorb":{"retrieved_at":"invented"}}')
        self.prepare("--source-metadata", metadata, code=1)
        self.assertFalse(self.output.exists())
        baseline.write_text(json.dumps({k: 100 for k in ("orbital_records", "discovery_records", "master_records", "known_discovery")}))
        self.assertIn("decreased", self.prepare("--baseline-counts", baseline, code=1)["error"])
        self.assertFalse((self.output / "latest.json").exists())
        self.prepare("--baseline-counts", baseline, "--allow-count-decrease")
        missing = self.directory / "missing/bundle"
        self.verify(missing, code=1)
        self.assertFalse(missing.parent.exists())
        self.cli("prepare-release", "--snapshot", "invalid", "--mpcorb", "missing", "--producer-commit", COMMIT, code=1)
        self.cli("prepare-release", "--snapshot", "invalid", "--mpcorb-url", "http://unused", "--producer-commit", COMMIT, code=1)

    def test_resealed_release_provenance_rejected(self):
        first = self.prepare()
        original = json.loads((Path(first["path"]) / "release.json").read_text())
        mutations = [("missing-" + key, (key,), None, True) for key in original]
        mutations += [
            ("extra-field", ("unexpected",), True, False),
            ("schema-bool", ("release_schema_version",), True, False),
            ("created-null", ("created_at",), None, False),
            ("created-invalid", ("created_at",), "2026-02-30T12:00:00Z", False),
            ("created-no-zone", ("created_at",), "2026-09-12T12:00:00", False),
            ("created-before-build", ("created_at",), "2000-01-01T00:00:00Z", False),
            ("runtime-list", ("runtime",), [], False),
            ("runtime-missing", ("runtime", "python"), None, True),
            ("runtime-extra", ("runtime", "unexpected"), "1.0.0", False),
            ("runtime-empty", ("runtime", "python"), " ", False),
            ("runtime-invalid", ("runtime", "python"), "invented", False),
            ("runtime-number", ("runtime", "sqlite"), 3, False),
            ("runtime-sqlite-mismatch", ("runtime", "sqlite"), "0.0.0", False),
            ("runtime-zlib-mismatch", ("runtime", "zlib"), "0.0.0", False),
            ("preparation-list", ("preparation",), [], False),
            ("preparation-missing", ("preparation", "baseline_counts"), None, True),
            ("preparation-extra", ("preparation", "unexpected"), None, False),
            ("override-string", ("preparation", "allow_count_decrease"), "false", False),
            ("override-number", ("preparation", "allow_count_decrease"), 0, False),
            ("baseline-partial", ("preparation", "baseline_counts"), {"orbital_records": 9}, False),
            ("baseline-bool", ("preparation", "baseline_counts"),
             {"orbital_records": True, "discovery_records": 6, "master_records": 9, "known_discovery": 6}, False),
            ("baseline-decrease", ("preparation", "baseline_counts"),
             {"orbital_records": 10, "discovery_records": 7, "master_records": 10, "known_discovery": 7}, False),
            ("previous-partial", ("preparation", "previous_counts"), {"master_records": 9}, False),
            ("previous-negative", ("preparation", "previous_counts"), {**original["counts"], "missing_discovery": -1}, False),
            ("previous-inconsistent", ("preparation", "previous_counts"), {**original["counts"], "orbital_records": 10}, False),
            ("previous-decrease", ("preparation", "previous_counts"),
             {**original["counts"], "orbital_records": 10, "master_records": 10,
              "unnumbered_orbits": 4, "missing_discovery": 4}, False),
        ]
        for label, keys, value, remove in mutations:
            with self.subTest(mutation=label):
                copied = self.directory / label
                shutil.copytree(first["path"], copied)
                manifest = json.loads(json.dumps(original))
                target = manifest
                for key in keys[:-1]:
                    target = target[key]
                if remove:
                    del target[keys[-1]]
                else:
                    target[keys[-1]] = value
                (copied / "release.json").write_text(json.dumps(manifest))
                # Reseal only the root checksum list, even when the inventory is missing.
                names = sorted([*original["artifacts"], "release.json"])
                (copied / "SHA256SUMS").write_text("".join(f"{file_info(copied / n)['sha256']}  {n}\n" for n in names))
                before = self.tree(copied)
                self.verify(copied, code=1)
                self.assertEqual(self.tree(copied), before)

    def test_orphan_with_invalid_provenance_cannot_be_activated(self):
        first = self.prepare()
        bundle = Path(first["path"])
        manifest_path = bundle / "release.json"
        original = manifest_path.read_bytes()
        manifest = json.loads(original)
        del manifest["preparation"]
        manifest_path.write_text(json.dumps(manifest))
        self.reseal(bundle)
        (self.output / "latest.json").unlink()
        before = self.tree(self.output)
        self.prepare(offline=first["snapshot_version"], code=1)
        self.assertEqual(self.tree(self.output), before)
        self.assertFalse((self.output / "latest.json").exists())
        manifest_path.write_bytes(original)
        self.reseal(bundle)
        self.verify(self.prepare(offline=first["snapshot_version"])["path"])

    def test_provenance_accepts_recorded_runtime_and_explicit_count_override(self):
        baseline = self.directory / "baseline.json"
        baseline.write_text(json.dumps({"orbital_records": 10, "discovery_records": 7,
                                       "master_records": 10, "known_discovery": 7}))
        script = ("import platform, sqlite3, zlib\n"
                  "platform.python_version = lambda: '3.11.0'\n"
                  "sqlite3.sqlite_version = '3.40.0'\nzlib.ZLIB_VERSION = '9.9.9'\n"
                  "zlib.ZLIB_RUNTIME_VERSION = '1.3'\n"
                  "from orrery_data.cli import main\nraise SystemExit(main())")
        first = self.prepare("--baseline-counts", baseline, "--allow-count-decrease", script=script)
        bundle = Path(first["path"])
        self.assertEqual(json.loads((bundle / "release.json").read_text())["runtime"]["zlib"], "1.3")
        self.assertEqual(json.loads((bundle / "snapshot.json").read_text())["compression"]["zlib"], "1.3")
        for profile in ("full", "first-2"):
            compression = json.loads((bundle / f"exports/{profile}/manifest.json").read_text())["compression"]
            self.assertEqual(compression["master"]["zlib"], "1.3")
            self.assertEqual(compression["catalog"]["zlib"], "1.3")
        self.verify(first["path"])
        # Normal verification runs in this machine's runtime, not the recorded one.
        self.assertEqual(self.prepare(offline=first["snapshot_version"]), first)

    def test_refresh_rejects_same_store_and_output_before_side_effects(self):
        message = "Source store and release output must be different directories when refreshing"
        self.assertIn(message, self.prepare("--output", self.store, code=1)["error"])
        self.assertFalse(self.store.exists())
        self.assertEqual(self.requests, [])

        first = self.prepare()
        before_store, before_output = self.tree(self.store), self.tree(self.output)
        self.requests.clear()
        store_link = self.directory / "linked-store"
        store_link.symlink_to(self.store, target_is_directory=True)
        parent_link = self.directory / "linked-parent"
        parent_link.symlink_to(self.directory, target_is_directory=True)
        for store, output in (
            (self.store, self.store),
            (self.store, self.store / ".." / self.store.name),
            (Path(os.path.relpath(self.store, ROOT)), self.store),
            (store_link, self.store),
            (self.store, parent_link / self.store.name),
        ):
            with self.subTest(store=store, output=output):
                self.assertIn(message, self.prepare("--store", store, "--output", output, code=1)["error"])
                self.assertEqual(self.tree(self.store), before_store)
                self.assertEqual(self.tree(self.output), before_output)
                self.assertEqual(self.requests, [])
        self.assertEqual(self.prepare(), first)

    def test_pinned_preparation_can_share_store_and_output(self):
        snapshot = self.cli("refresh", "--store", self.store, *self.http_args())["snapshot_version"]
        before_store = self.tree(self.store)
        self.requests.clear()
        first = self.prepare("--output", self.store, offline=snapshot)
        self.verify(first["path"])
        self.assertEqual(self.prepare("--output", self.store, offline=snapshot), first)
        after_store = self.tree(self.store)
        self.assertEqual({name: after_store[name] for name in before_store}, before_store)
        self.assertEqual(self.requests, [])

    def test_locks_partial_downloads_and_output_placement(self):
        first = self.prepare()
        before = self.tree(self.output)
        with (self.output / ".lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.assertIn("Another writer", self.prepare(code=1)["error"])
        self.responses["/orbits"]["headers"]["Content-Length"] = "99999999"
        self.prepare(code=1)
        self.assertEqual(self.tree(self.output), before)
        self.prepare("--output", self.store / "snapshots" / first["snapshot_version"],
                     offline=first["snapshot_version"], code=1)
        link = self.directory / "linked-output"
        link.symlink_to(self.output, target_is_directory=True)
        self.prepare("--output", link, offline=first["snapshot_version"], code=1)

    def workflow(self, env, code=0, external_cwd=False):
        result = subprocess.run([sys.executable, str(ROOT / "scripts/prepare_release_workflow.py"), "--work-dir",
                                 "workflow" if external_cwd else str(self.directory / "workflow"),
                                 *self.http_args()], cwd=self.directory if external_cwd else ROOT, text=True, capture_output=True,
                                env={**os.environ, **env}, timeout=30)
        self.assertEqual(result.returncode, code, result.stdout + result.stderr)
        return result

    def test_workflow_real_http_preparation_rerun_and_failure_outputs(self):
        output = self.directory / "github-output"
        summary = self.directory / "github-summary"
        baseline = {"orbital_records": 9, "discovery_records": 6, "master_records": 9, "known_discovery": 6}
        env = {"RELEASE_PRODUCER_COMMIT": COMMIT, "RELEASE_BASELINE_COUNTS": json.dumps(baseline),
               "RELEASE_SELECTED_LIMITS": "2,4", "RELEASE_ALLOW_COUNT_DECREASE": "false",
               "GITHUB_OUTPUT": str(output), "GITHUB_STEP_SUMMARY": str(summary)}
        self.workflow(env)
        prepared = json.loads((self.directory / "workflow/prepared-release.json").read_text())
        self.assertIn("bundle=" + prepared["path"], output.read_text())
        self.assertIn("No GitHub Release was published", summary.read_text())
        before = self.tree(Path(prepared["path"]))
        self.workflow(env)
        self.assertEqual(self.tree(Path(prepared["path"])), before)
        output.write_text("")
        for field, value in (("RELEASE_SELECTED_LIMITS", "2;touch injected"),
                             ("RELEASE_BASELINE_COUNTS", "{}"), ("RELEASE_ALLOW_COUNT_DECREASE", "yes"),
                             ("RELEASE_BASELINE_COUNTS", json.dumps(baseline).replace('"known_discovery":', '"known_discovery": 0, "known_discovery":')),
                             ("RELEASE_PRODUCER_COMMIT", "master")):
            self.workflow({**env, field: value}, code=1)
            self.assertEqual(output.read_text(), "")
        self.responses["/dates"]["status"] = 503
        self.workflow(env, code=1)
        self.assertEqual(output.read_text(), "")
        self.assertEqual(self.tree(Path(prepared["path"])), before)

    def test_workflow_relative_work_directory_from_another_cwd(self):
        env = {"RELEASE_PRODUCER_COMMIT": COMMIT,
               "RELEASE_BASELINE_COUNTS": json.dumps({"orbital_records": 9, "discovery_records": 6,
                                                     "master_records": 9, "known_discovery": 6}),
               "GITHUB_OUTPUT": "", "GITHUB_STEP_SUMMARY": ""}
        self.workflow(env, external_cwd=True)
        result = json.loads((self.directory / "workflow/prepared-release.json").read_text())
        self.assertTrue(Path(result["path"]).is_relative_to(self.directory.resolve()))
        self.verify(result["path"])

    def test_prepare_help_describes_refresh_and_offline_pin(self):
        result = subprocess.run([sys.executable, "-m", "orrery_data", "prepare-release", "--help"],
                                cwd=ROOT, text=True, capture_output=True, check=True)
        self.assertIn("otherwise refresh sources", " ".join(result.stdout.split()))
        self.assertNotIn("default: current", result.stdout)
