"""Real release boundaries for orbital schemas and immutable output placement."""

import gzip
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import unittest

import test_releases
from orrery_data.formats import DataError
from orrery_data.releases import prepare_release
from orrery_data.storage import digest, file_info

ROOT = Path(__file__).resolve().parents[1]


class ReleaseReview(unittest.TestCase):
    setUpClass = classmethod(test_releases.ReleaseCLI.setUpClass.__func__)
    tearDownClass = classmethod(test_releases.ReleaseCLI.tearDownClass.__func__)
    setUp = test_releases.ReleaseCLI.setUp
    cli = test_releases.ReleaseCLI.cli
    prepare = test_releases.ReleaseCLI.prepare
    verify = test_releases.ReleaseCLI.verify
    tree = test_releases.ReleaseCLI.tree
    http_args = test_releases.ReleaseCLI.http_args
    reseal = test_releases.ReleaseCLI.reseal

    def write_root_manifest(self, bundle, manifest):
        # Preserve deliberately malformed root metadata while resealing transport hashes.
        (bundle / "release.json").write_text(json.dumps(manifest))
        names = sorted([*manifest["artifacts"], "release.json"])
        (bundle / "SHA256SUMS").write_text("".join(f"{file_info(bundle / n)['sha256']}  {n}\n" for n in names))

    def change_master_compression(self, bundle, profile):
        directory = bundle / "exports" / profile
        manifest = json.loads((directory / "manifest.json").read_text())
        manifest["compression"]["master"]["zlib"] = "9.9.9"
        (directory / "manifest.json").write_text(json.dumps(manifest))
        names = sorted([*manifest["artifacts"], "manifest.json"])
        (directory / "SHA256SUMS").write_text("".join(f"{file_info(directory / n)['sha256']}  {n}\n" for n in names))
        self.reseal(bundle)

    def test_root_artifact_metadata_rejects_unknown_fields_and_orphan_reuse(self):
        first = self.prepare()
        bundle = Path(first["path"])
        original = json.loads((bundle / "release.json").read_text())
        for index, name in enumerate(original["artifacts"]):
            with self.subTest(artifact=name):
                copied = self.directory / f"root-artifact-{index}"
                shutil.copytree(bundle, copied)
                manifest = json.loads(json.dumps(original))
                manifest["artifacts"][name]["future"] = "unsupported extension"
                self.write_root_manifest(copied, manifest)
                before = self.tree(copied)
                error = self.verify(copied, code=1)["error"]
                self.assertIn("Invalid release artifact metadata fields", error)
                self.assertIn(name, error)
                self.assertEqual(self.tree(copied), before)
        saved = {name: (bundle / name).read_bytes() for name in ("release.json", "SHA256SUMS")}
        (self.output / "latest.json").unlink()
        original["artifacts"]["orrery.sqlite3"]["future"] = 1
        self.write_root_manifest(bundle, original)
        before = self.tree(self.output)
        self.assertIn("Invalid release artifact metadata fields", self.prepare(offline=first["snapshot_version"], code=1)["error"])
        self.assertEqual(self.tree(self.output), before)
        for name, payload in saved.items():
            (bundle / name).write_bytes(payload)
        self.assertEqual(self.prepare(offline=first["snapshot_version"]), first)

    def test_master_compression_must_match_snapshot_for_every_profile_and_reuse(self):
        first = self.prepare()
        bundle = Path(first["path"])
        for profile in ("full", "first-2"):
            with self.subTest(profile=profile):
                copied = self.directory / f"master-compression-{profile}"
                shutil.copytree(bundle, copied)
                self.change_master_compression(copied, profile)
                before = self.tree(copied)
                error = self.verify(copied, code=1)["error"]
                self.assertIn(f"exports/{profile}: master compression differs from snapshot", error)
                self.assertEqual(self.tree(copied), before)
        saved = {name: (bundle / name).read_bytes() for name in
                 ("release.json", "SHA256SUMS", "exports/full/manifest.json", "exports/full/SHA256SUMS")}
        (self.output / "latest.json").unlink()
        self.change_master_compression(bundle, "full")
        before = self.tree(self.output)
        self.assertIn("master compression differs from snapshot", self.prepare(offline=first["snapshot_version"], code=1)["error"])
        self.assertEqual(self.tree(self.output), before)
        for name, payload in saved.items():
            (bundle / name).write_bytes(payload)
        self.assertEqual(self.prepare(offline=first["snapshot_version"]), first)

    def change_catalog(self, bundle, profile, values):
        directory = bundle / "exports" / profile
        rows = json.loads((directory / "catalog.json").read_text())
        rows[0].update(values)
        payload = (json.dumps(rows) + "\n").encode()
        (directory / "catalog.json").write_bytes(payload)
        (directory / "catalog.json.gz").write_bytes(gzip.compress(payload, mtime=0))
        manifest = json.loads((directory / "manifest.json").read_text())
        for name in ("catalog.json", "catalog.json.gz"):
            manifest["artifacts"][name].update(file_info(directory / name))
        (directory / "manifest.json").write_text(json.dumps(manifest))
        names = sorted([*manifest["artifacts"], "manifest.json"])
        (directory / "SHA256SUMS").write_text("".join(f"{file_info(directory / n)['sha256']}  {n}\n" for n in names))
        self.reseal(bundle)

    def test_resealed_catalog_rejects_invalid_orbital_values(self):
        first = self.prepare()
        invalid = [("a", 0), ("a", -1), ("e", -0.1), ("e", 1), ("n", 0), ("n", -1),
                   ("i", -0.1), ("i", 180.1)]
        invalid += [(key, value) for key in ("W", "w", "M") for value in (-0.1, 360.1)]
        for profile in ("full", "first-2"):
            for index, (key, value) in enumerate(invalid):
                with self.subTest(profile=profile, key=key, value=value):
                    copied = self.directory / f"{profile}-{index}"
                    shutil.copytree(first["path"], copied)
                    self.change_catalog(copied, profile, {key: value})
                    before = self.tree(copied)
                    self.assertIn("Invalid discovery orbital elements", self.verify(copied, code=1)["error"])
                    self.assertEqual(self.tree(copied), before)

    def test_resealed_catalog_accepts_supported_orbital_boundaries(self):
        first = self.prepare()
        for index, values in enumerate((
            {"a": 1e-10, "e": 0, "n": 1e-10, "i": 0, "W": 0, "w": 0, "M": 0},
            {"a": 1, "e": 0.999999, "n": 1, "i": 180, "W": 360, "w": 360, "M": 360},
        )):
            copied = self.directory / f"boundary-{index}"
            shutil.copytree(first["path"], copied)
            for profile in ("full", "first-2"):
                self.change_catalog(copied, profile, values)
            self.verify(copied)

    def test_resealed_identity_schema_versions_require_integers(self):
        first = self.prepare()
        for key in ("release_schema_version", "json_schema_version", "database_schema_version"):
            for value in (True, 1.0):
                with self.subTest(key=key, value=value):
                    copied = self.directory / f"{key}-{value}"
                    shutil.copytree(first["path"], copied)
                    manifest = json.loads((copied / "release.json").read_text())
                    manifest["identity"][key] = value
                    manifest["release_version"] = "release-v1-" + digest(manifest["identity"])
                    (copied / "release.json").write_text(json.dumps(manifest))
                    self.reseal(copied)
                    self.assertIn("schema version", self.verify(copied, code=1)["error"])

    def test_resealed_identity_requires_exact_schema_one_fields(self):
        first = self.prepare()
        bundle = Path(first["path"])
        original = json.loads((bundle / "release.json").read_text())
        cases = [("identity", "future", 1), ("producer", "future", 1)]
        for target in ("identity", "producer"):
            value = original["identity"] if target == "identity" else original["identity"]["producer"]
            cases.extend((target, key, None) for key in value)
        cases.extend(("producer-value", "producer", value) for value in (None, [], "producer", 1, True))
        for index, (target, key, value) in enumerate(cases):
            with self.subTest(target=target, key=key, value=value):
                copied = self.directory / f"identity-{index}"
                shutil.copytree(bundle, copied)
                manifest = json.loads(json.dumps(original))
                obj = manifest["identity"]["producer"] if target == "producer" else manifest["identity"]
                if value is None and target != "producer-value":
                    del obj[key]
                else:
                    obj[key] = value
                manifest["release_version"] = "release-v1-" + digest(manifest["identity"])
                (copied / "release.json").write_text(json.dumps(manifest))
                self.reseal(copied)
                before = self.tree(copied)
                self.assertIn("fields", self.verify(copied, code=1)["error"])
                self.assertEqual(self.tree(copied), before)
        # Preparation uses the same verifier before reusing/activating a candidate.
        original_bytes = (bundle / "release.json").read_bytes()
        original_checksums = (bundle / "SHA256SUMS").read_bytes()
        (self.output / "latest.json").unlink()
        original["identity"]["producer"]["future"] = 1
        original["release_version"] = "release-v1-" + digest(original["identity"])
        (bundle / "release.json").write_text(json.dumps(original))
        self.reseal(bundle)
        before = self.tree(self.output)
        self.assertIn("fields", self.prepare(offline=first["snapshot_version"], code=1)["error"])
        self.assertEqual(self.tree(self.output), before)
        (bundle / "release.json").write_bytes(original_bytes)
        (bundle / "SHA256SUMS").write_bytes(original_checksums)
        self.assertEqual(self.prepare(offline=first["snapshot_version"]), first)

    def test_preparation_cannot_write_inside_existing_candidates(self):
        first = self.prepare()
        bundle = Path(first["path"])
        copied = self.directory / "renamed-copy"
        shutil.copytree(bundle, copied)
        incomplete = self.directory / ("release-v1-" + "a" * 64)
        incomplete.mkdir()
        (incomplete / "keep").write_text("incomplete candidate evidence")
        alias = self.directory / "output-alias"
        alias.symlink_to(self.output, target_is_directory=True)
        before = {root: self.tree(root) for root in (self.store, self.output, copied, incomplete)}
        self.requests.clear()
        for target in (bundle, bundle / "exports/full", bundle / "new/deep",
                       Path(os.path.relpath(bundle, ROOT)), bundle / ".." / bundle.name,
                       alias / bundle.name / "new", copied, copied / "new", incomplete / "new"):
            for offline in (None, first["snapshot_version"]):
                with self.subTest(target=target, offline=offline):
                    error = self.prepare("--output", target, offline=offline, code=1)["error"]
                    self.assertIn("existing release candidate", error)
                    self.assertEqual(self.requests, [])
                    for root, tree in before.items():
                        self.assertEqual(self.tree(root), tree)
        self.assertEqual(self.prepare(offline=first["snapshot_version"]), first)

    def test_explicit_snapshot_pins_are_validated_before_side_effects(self):
        first = self.prepare()
        before_store, before_output = self.tree(self.store), self.tree(self.output)
        self.requests.clear()
        invalid = ("", " ", "current", "../current", "snapshot-v1-" + "a" * 63,
                   "snapshot-v1-" + "A" * 64, first["snapshot_version"] + "\n")
        for store in (self.store, self.directory / "missing-store"):
            for output in (self.output, self.directory / "missing-output"):
                for value in invalid:
                    with self.subTest(store=store, output=output, value=value):
                        error = self.cli("prepare-release", "--store", store, "--output", output,
                                         "--producer-commit", test_releases.COMMIT, "--snapshot", value, code=1)["error"]
                        self.assertIn("Snapshot pin must be", error)
                        self.assertEqual(self.tree(self.store), before_store)
                        self.assertEqual(self.tree(self.output), before_output)
                        self.assertFalse((self.directory / "missing-store").exists())
                        self.assertFalse((self.directory / "missing-output").exists())
                        self.assertEqual(self.requests, [])
        # Empty explicitly supplied pins also obey the CLI's acquisition exclusion.
        error = self.cli("prepare-release", "--store", self.store, "--output", self.output,
                         "--producer-commit", test_releases.COMMIT, "--snapshot", "", *self.http_args(), code=1)["error"]
        self.assertIn("--snapshot cannot be combined", error)
        self.assertEqual(self.requests, [])
        self.assertEqual(self.tree(self.store), before_store)
        self.assertEqual(self.tree(self.output), before_output)
        # A valid explicit pin ignores even an unusable mutable current pointer.
        current = self.store / "current.json"
        pointer = current.read_bytes()
        current.write_text(json.dumps({"snapshot_version": "snapshot-v1-" + "f" * 64}))
        try:
            self.assertEqual(self.prepare(offline=first["snapshot_version"]), first)
            self.assertEqual(self.requests, [])
        finally:
            current.write_bytes(pointer)

    def test_programmatic_snapshot_pins_require_strings(self):
        for version in (False, True, 0, 1, [], {}, b""):
            with self.subTest(version=version):
                with self.assertRaisesRegex(DataError, "Snapshot pin must be"):
                    prepare_release(self.store, self.output, test_releases.COMMIT, version=version)
                self.assertFalse(self.store.exists())
                self.assertFalse(self.output.exists())

    def test_latest_pointer_shapes_and_foreign_export_root_have_clear_errors(self):
        first = self.prepare()
        pointer = self.output / "latest.json"
        original = pointer.read_bytes()
        invalid = [[], {}, {"data_version": "export-v1-test"}, {"release_version": first["release_version"]},
                   {"release_version": "../bad", "manifest": first["manifest"]}]
        invalid += [{"release_version": first["release_version"], "manifest": info}
                    for info in ([], {}, {"sha256": "bad", "bytes": 1}, {"sha256": "f" * 64, "bytes": True},
                                 {**first["manifest"], "extra": 1})]
        for value in invalid:
            with self.subTest(pointer=value):
                pointer.write_text(json.dumps(value))
                before = self.tree(self.output)
                self.assertIn("latest", self.prepare(offline=first["snapshot_version"], code=1)["error"])
                self.assertEqual(self.tree(self.output), before)
        pointer.write_bytes(original)
        foreign = self.directory / "web-exports"
        self.cli("export", "--store", self.store, "--output", foreign)
        before = self.tree(foreign)
        self.assertIn("separate release output", self.prepare("--output", foreign, offline=first["snapshot_version"], code=1)["error"])
        self.assertEqual(self.tree(foreign), before)

    def test_repeat_verifies_once_and_missing_previous_bundle_explains_recovery(self):
        first = self.prepare()
        calls = self.directory / "verification-calls"
        script = ("from pathlib import Path\nimport orrery_data.releases as releases\n"
                  "from orrery_data.cli import main\noriginal = releases.verify_release\n"
                  "def verify(*args, **kwargs):\n"
                  f" with Path({str(calls)!r}).open('a') as stream: stream.write('verify\\n')\n"
                  " return original(*args, **kwargs)\nreleases.verify_release = verify\nraise SystemExit(main())")
        self.assertEqual(self.prepare(offline=first["snapshot_version"], script=script), first)
        self.assertEqual(calls.read_text().splitlines(), ["verify"])
        hidden = self.directory / "saved-candidate"
        Path(first["path"]).rename(hidden)
        before = self.tree(self.output)
        error = self.prepare(offline=first["snapshot_version"], code=1)["error"]
        self.assertIn("latest.json", error)
        self.assertIn("--baseline-counts", error)
        self.assertEqual(self.tree(self.output), before)
        hidden.rename(first["path"])
        self.assertEqual(self.prepare(offline=first["snapshot_version"]), first)

    def test_export_and_database_reject_empty_pins_without_output(self):
        first = self.prepare()
        before = self.tree(self.store)
        output = self.directory / "empty-pin-output"
        for command, flags in (("export", ["--output", output]), ("build-db", ["--database", output / "data.sqlite3"])):
            for value in ("", " ", "../bad"):
                with self.subTest(command=command, value=value):
                    error = self.cli(command, "--store", self.store, "--snapshot", value, *flags, code=1)["error"]
                    self.assertIn("No valid snapshot selected", error)
                    self.assertFalse(output.exists())
                    self.assertEqual(self.tree(self.store), before)
            self.cli(command, "--store", self.store, "--snapshot", first["snapshot_version"], *flags)
            shutil.rmtree(output)

    def test_full_count_baselines_and_default_programmatic_urls(self):
        first = self.prepare()
        baseline = self.directory / "baseline.json"
        baseline.write_text(json.dumps(first["counts"]))
        prepared = self.prepare("--output", self.directory / "full-baseline", "--baseline-counts", baseline,
                                offline=first["snapshot_version"])
        manifest = json.loads((Path(prepared["path"]) / "release.json").read_text())
        self.assertEqual(manifest["preparation"]["baseline_counts"],
                         {key: first["counts"][key] for key in ("orbital_records", "discovery_records", "master_records", "known_discovery")})
        # The flexible input is normalized; schema-1 manifests still record four fields.
        manifest["preparation"]["baseline_counts"] = first["counts"]
        (Path(prepared["path"]) / "release.json").write_text(json.dumps(manifest))
        self.reseal(Path(prepared["path"]))
        self.assertIn("baseline fields", self.verify(prepared["path"], code=1)["error"])
        output = self.directory / "api-output"
        source = self.directory / "api-store"
        mpcorb, numbered = self.directory / "mpcorb", self.directory / "numbered"
        mpcorb.write_text(self.orbits)
        numbered.write_text(self.dates)
        self.requests.clear()
        result = prepare_release(source, output, test_releases.COMMIT, local={"mpcorb": mpcorb, "numbered": numbered})
        self.assertEqual(result["counts"], first["counts"])
        self.assertEqual(self.requests, [])

    def test_pinned_timeout_is_rejected_even_at_default_value(self):
        first = self.prepare()
        before = self.tree(self.output)
        for timeout in (5, 60):
            self.assertIn("source acquisition", self.prepare("--timeout", timeout, offline=first["snapshot_version"], code=1)["error"])
        self.assertEqual(self.tree(self.output), before)

    def test_malformed_nested_manifests_and_gzip_have_contextual_json_errors(self):
        first = self.prepare()
        cases = [("snapshot.json", [], "snapshot manifest"),
                 ("snapshot.json", ("sources", ["mpcorb", "numbered"]), "sources"),
                 ("snapshot.json", ("files", []), "files"),
                 ("snapshot.json", ("counts", []), "count fields"),
                 ("snapshot.json", ("exclusions", []), "exclusions"),
                 ("snapshot.json", ("compression", []), "compression"),
                 ("exports/full/manifest.json", [], "manifest fields"),
                 ("exports/full/manifest.json", ("compression", None), "manifest fields"),
                 ("exports/full/manifest.json", ("artifacts", []), "manifest fields")]
        for index, (name, change, diagnostic) in enumerate(cases):
            with self.subTest(file=name, change=change):
                copied = self.directory / f"shape-{index}"
                shutil.copytree(first["path"], copied)
                path = copied / name
                value = json.loads(path.read_text())
                if isinstance(change, tuple):
                    value[change[0]] = change[1]
                else:
                    value = change
                path.write_text(json.dumps(value))
                self.reseal(copied)
                before = self.tree(copied)
                error = self.verify(copied, code=1)["error"]
                self.assertIn(name, error)
                self.assertIn(diagnostic, error)
                self.assertEqual(self.tree(copied), before)
        for case in ("missing-bytes", "bad-gzip"):
            copied = self.directory / case
            shutil.copytree(first["path"], copied)
            directory = copied / "exports/full"
            manifest = json.loads((directory / "manifest.json").read_text())
            if case == "missing-bytes":
                del manifest["artifacts"]["catalog.json.gz"]["bytes"]
            else:
                (directory / "catalog.json.gz").write_bytes(b"not gzip")
                manifest["artifacts"]["catalog.json.gz"].update(file_info(directory / "catalog.json.gz"))
            (directory / "manifest.json").write_text(json.dumps(manifest))
            names = sorted([*manifest["artifacts"], "manifest.json"])
            (directory / "SHA256SUMS").write_text("".join(f"{file_info(directory / n)['sha256']}  {n}\n" for n in names))
            self.reseal(copied)
            error = self.verify(copied, code=1)["error"]
            self.assertIn("exports/full", error)
            self.assertIn("catalog.json.gz", error)
            self.assertIn("metadata" if case == "missing-bytes" else "Invalid catalog", error)
        empty = self.directory / "empty-bundle"
        empty.mkdir()
        self.assertIn("Bundle file inventory mismatch", self.verify(empty, code=1)["error"])

    def test_snapshot_source_shape_and_count_errors_remain_json_for_all_consumers(self):
        first = self.prepare()
        path = self.store / "snapshots" / first["snapshot_version"] / "snapshot.json"
        original = json.loads(path.read_text())
        for key, value, diagnostic in (("sources", ["mpcorb", "numbered"], "sources"),
                                       ("counts", {**original["counts"], "extra": 1}, "count fields"),
                                       ("counts", {**original["counts"], "known_discovery": 0}, "do not reconcile")):
            path.write_text(json.dumps({**original, key: value}))
            for command, flags in (("check", self.http_args()), ("refresh", self.http_args()),
                                   ("export", ["--output", self.directory / "exports-invalid"]),
                                   ("build-db", ["--database", self.directory / "invalid-db/data.sqlite3"])):
                with self.subTest(key=key, command=command):
                    error = self.cli(command, "--store", self.store, *flags, code=1)["error"]
                    self.assertIn(diagnostic, error)
            self.assertFalse((self.directory / "exports-invalid").exists())
            self.assertFalse((self.directory / "invalid-db").exists())

    def test_small_clock_reversal_preserves_actual_times_and_large_skew_rejects(self):
        script = ("from unittest.mock import patch\nfrom orrery_data.cli import main\n"
                  "with patch('orrery_data.database.now', return_value='2026-09-13T00:00:05Z'), "
                  "patch('orrery_data.pipeline.now', return_value='2026-09-13T00:00:04Z'), "
                  "patch('orrery_data.releases.now', return_value='2026-09-13T00:00:03Z'):\n"
                  " raise SystemExit(main())")
        first = self.prepare(script=script)
        bundle = Path(first["path"])
        self.verify(bundle)
        manifest = json.loads((bundle / "release.json").read_text())
        self.assertEqual(manifest["created_at"], "2026-09-13T00:00:03Z")
        self.assertEqual(self.cli("db-info", "--database", bundle / "orrery.sqlite3")["created_at"], "2026-09-13T00:00:05Z")
        manifest["created_at"] = "2026-09-12T23:59:59Z"
        (bundle / "release.json").write_text(json.dumps(manifest))
        self.reseal(bundle)
        self.assertIn("5-second clock tolerance", self.verify(bundle, code=1)["error"])

    def test_workflow_preflight_requires_environment_and_accepts_spaced_limits(self):
        first = self.prepare()
        work = self.directory / "workflow-preflight"
        base = {**os.environ, "RELEASE_PRODUCER_COMMIT": test_releases.COMMIT,
                "RELEASE_BASELINE_COUNTS": json.dumps(first["counts"]), "RELEASE_SELECTED_LIMITS": " 2, 4, ",
                "GITHUB_OUTPUT": "", "GITHUB_STEP_SUMMARY": ""}
        invocation = [sys.executable, str(ROOT / "scripts/prepare_release_workflow.py"), "--work-dir", str(work), *self.http_args()]
        for key in ("RELEASE_PRODUCER_COMMIT", "RELEASE_BASELINE_COUNTS"):
            env = {k: v for k, v in base.items() if k != key}
            result = subprocess.run(invocation, cwd=ROOT, env=env, capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 1)
            self.assertIn(f"Environment variable {key} is required", result.stderr)
            self.assertFalse(work.exists())
        for key, value in (("RELEASE_PRODUCER_COMMIT", "master"), ("RELEASE_SELECTED_LIMITS", ", ,")):
            result = subprocess.run(invocation, cwd=ROOT, env={**base, key: value}, capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 1)
            self.assertFalse(work.exists())
        result = subprocess.run(invocation, cwd=ROOT, env=base, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        prepared = json.loads((work / "prepared-release.json").read_text())
        self.assertEqual(set(prepared["profiles"]), {"full", "first-2", "first-4"})

    def test_pinned_snapshot_preserves_original_compression_provenance(self):
        first = self.prepare()
        original = json.loads((Path(first["path"]) / "snapshot.json").read_text())
        script = ("import zlib\nzlib.ZLIB_VERSION = '9.9.9'\nzlib.ZLIB_RUNTIME_VERSION = '1.3'\n"
                  "from orrery_data.cli import main\nraise SystemExit(main())")
        second = self.prepare("--producer-commit", "2" * 40, offline=first["snapshot_version"], script=script)
        bundle = Path(second["path"])
        self.assertEqual(json.loads((bundle / "release.json").read_text())["runtime"]["zlib"], "1.3")
        self.assertEqual(json.loads((bundle / "snapshot.json").read_text()), original)
        for profile in ("full", "first-2"):
            compression = json.loads((bundle / f"exports/{profile}/manifest.json").read_text())["compression"]
            self.assertEqual(compression["master"], original["compression"])
            self.assertEqual(compression["catalog"]["zlib"], "1.3")
        self.verify(bundle)
