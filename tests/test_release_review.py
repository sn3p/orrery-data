"""Real release boundaries for orbital schemas and immutable output placement."""

import gzip
import json
import os
from pathlib import Path
import shutil
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
