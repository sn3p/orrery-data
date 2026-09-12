"""Real release boundaries for orbital schemas and immutable output placement."""

import gzip
import json
import os
from pathlib import Path
import shutil
import unittest

import test_releases
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
