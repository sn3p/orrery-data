"""Legacy saved-data scripts must own every destination before their first write."""

import ast
import gzip
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unicodedata
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ("validate_saved_snapshot.py", "validate_saved_database.py")


def tree(root):
    return {str(path.relative_to(root)): path.read_bytes()
            for path in root.rglob("*") if path.is_file()}


REPORT_FAULT = '''
import os, runpy, sys
from pathlib import Path
from unittest.mock import patch
script, report, fault = sys.argv[1:4]
sys.argv = [script, *sys.argv[4:]]
sys.path.insert(0, str(Path(script).resolve().parents[1]))
import orrery_data.storage as storage
original = storage.atomic_json
real_fdopen = os.fdopen
class PartialWrite:
    def __init__(self, stream): self.stream = stream
    def __getattr__(self, name): return getattr(self.stream, name)
    def __enter__(self): return self
    def __exit__(self, *args): return self.stream.__exit__(*args)
    def write(self, value):
        self.stream.write(value[:20])
        self.stream.flush()
        raise OSError("injected report write failure")
def publish(path, value):
    if Path(path).resolve() != Path(report).resolve():
        return original(path, value)
    if fault == "write":
        with patch.object(os, "fdopen", side_effect=lambda *a, **kw: PartialWrite(real_fdopen(*a, **kw))):
            return original(path, value)
    with patch.object(os, fault, side_effect=OSError("injected report " + fault + " failure")):
        return original(path, value)
storage.atomic_json = publish
runpy.run_path(script, run_name="__main__")
'''


class SavedInputPaths(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.sources = self.directory / "sources"
        self.sources.mkdir()
        self.hashes = {}
        for key, fixture, basename in (("mpcorb", "MPCORB.DAT", "MPCORB"),
                                       ("numbered", "NumberedMPs.txt", "NumberedMPs")):
            payload = gzip.compress((ROOT / "tests/fixtures" / fixture).read_bytes(), mtime=0)
            (self.sources / (fixture + ".gz")).write_bytes(payload)
            (self.sources / (basename + ".headers")).write_text(
                'ETag: "fixture"\nLast-Modified: Sat, 12 Sep 2026 12:57:00 GMT\n'
                f"Content-Length: {len(payload)}\n")
            self.hashes[key] = hashlib.sha256(payload).hexdigest()
        self.store = self.directory / "retained-store"
        self.references = self.directory / "references"
        self.snapshot = self.cli("refresh", "--store", self.store,
                                 "--mpcorb", ROOT / "tests/fixtures/MPCORB.DAT",
                                 "--numbered", ROOT / "tests/fixtures/NumberedMPs.txt")
        for args in ((), ("--limit", "100000")):
            self.cli("export", "--store", self.store, "--output", self.references, *args)
        self.producer = self.directory / "producer"
        shutil.copytree(ROOT / "orrery_data", self.producer / "orrery_data", ignore=shutil.ignore_patterns("__pycache__"))
        (self.producer / "scripts").mkdir()
        shutil.copyfile(ROOT / "pyproject.toml", self.producer / "pyproject.toml")
        master = Path(self.snapshot["path"]) / "master.jsonl.gz"
        # Substitute only the retained-data expectations. All producers,
        # argument checks, filesystem writes and subprocess workflows are real.
        for name in SCRIPTS:
            source = (ROOT / "scripts" / name).read_text()
            values = {"HASHES": self.hashes, "MASTER_SHA256": hashlib.sha256(master.read_bytes()).hexdigest(),
                      "EXPECTED": ({key: self.snapshot["counts"][key] for key in (
                          "orbital_records", "master_records", "known_discovery", "missing_discovery",
                          "unsupported_orbits", "unmatched_discovery_records")}
                                   if "snapshot" in name else {key: self.snapshot["counts"][key] for key in (
                                       "master_records", "known_discovery", "missing_discovery")})}
            lines = source.splitlines(keepends=True)
            for node in reversed(ast.parse(source).body):
                if (isinstance(node, ast.Assign) and len(node.targets) == 1
                        and isinstance(node.targets[0], ast.Name) and node.targets[0].id in values):
                    key = node.targets[0].id
                    lines[node.lineno - 1:node.end_lineno] = [f"{key} = {values[key]!r}\n"]
            source = "".join(lines)
            source = source.replace("56229cf0f045beb62180a62f4b9a9351af9955e7e88aab5976256fa62347a705",
                                    hashlib.sha256((ROOT / "tests/fixtures/NumberedMPs.txt").read_bytes()).hexdigest())
            source = source.replace("(1563495, 895910, 667585)", "(9, 6, 3)")
            source = source.replace('(("all", 1563495), ("known", 895910), ("missing", 667585))',
                                    '(("all", 9), ("known", 6), ("missing", 3))')
            source = source.replace('"K17S44L"', '"J60S01B"').replace("== 360.0", "== 224.49081")
            source = source.replace('"--offset", 1563494', '"--offset", 8')
            (self.producer / "scripts" / name).write_text(source)

    def cli(self, *args):
        result = subprocess.run([sys.executable, "-B", "-m", "orrery_data", *map(str, args)],
                                cwd=ROOT, capture_output=True, text=True, check=True, timeout=30)
        return json.loads(result.stdout)

    def invoke(self, name, work, report, *, flags=(), optimization="0", fault=None):
        script = self.producer / "scripts" / name
        args = (["--sources", self.sources] if "snapshot" in name else
                ["--store", self.store, "--reference-exports", self.references])
        args += ["--work-dir", work, "--report", report]
        command = [sys.executable, "-B", *flags, script, *args]
        if fault:
            command = [sys.executable, "-B", "-c", REPORT_FAULT, script, report, fault, *args]
        return subprocess.run(list(map(str, command)), cwd=self.directory, text=True, capture_output=True,
                              timeout=30, env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "PYTHONOPTIMIZE": optimization})

    def assert_rejected(self, name, work, report):
        before = {root: tree(root) for root in (self.sources, self.store, self.references, self.producer)}
        result = self.invoke(name, work, report)
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertEqual(result.stdout, "")
        self.assertTrue("overlap" in result.stderr or "immutable" in result.stderr
                        or "ancestors" in result.stderr, result.stderr)
        for root, expected in before.items():
            self.assertEqual(tree(root), expected)

    def test_retained_inputs_and_producer_paths_reject_before_output(self):
        work, report = self.directory / "work", self.directory / "report.json"
        report.write_text("previous evidence")
        for name in SCRIPTS:
            root = self.sources if "snapshot" in name else self.store
            alias = self.directory / (name + "-alias")
            alias.symlink_to(root, target_is_directory=True)
            inputs = [root, alias / "new", self.directory, self.producer / "scripts",
                      self.producer / "orrery_data", self.producer / "pyproject.toml"]
            if "database" in name:
                inputs.append(self.references)
            for path in inputs:
                with self.subTest(script=name, work=path):
                    self.assert_rejected(name, path, report)
                    self.assertEqual(report.read_text(), "previous evidence")
                with self.subTest(script=name, report=path):
                    self.assert_rejected(name, work, path)
                    self.assertFalse(work.exists())

    def test_candidates_renamed_copies_and_aliases_are_unchanged(self):
        release = self.cli("prepare-release", "--store", self.store, "--snapshot", self.snapshot["snapshot_version"],
                           "--output", self.directory / "release-root", "--producer-commit", "1" * 40,
                           "--selected-limit", "2")
        bundle = Path(release["path"])
        renamed = self.directory / "renamed-bundle"
        shutil.copytree(bundle, renamed)
        alias = self.directory / "candidate-alias"
        alias.symlink_to(bundle, target_is_directory=True)
        candidates = [bundle, renamed, alias, Path(self.snapshot["path"]),
                      next(self.references.glob("export-*")), bundle / "exports/full"]
        previous = self.directory / "previous-report.json"
        previous.write_text("retained evidence")
        before = {root: tree(root) for root in (bundle, renamed, self.store, self.references)}
        for name in SCRIPTS:
            for candidate in candidates:
                for path in (candidate, candidate / "new/deep"):
                    with self.subTest(script=name, candidate=path):
                        self.assert_rejected(name, path, previous)
                        self.assert_rejected(name, self.directory / "new-work", path / "report.json")
                        self.assertFalse((self.directory / "new-work").exists())
                        self.assertEqual(previous.read_text(), "retained evidence")
                        for root, expected in before.items():
                            self.assertEqual(tree(root), expected)
        self.cli("verify-release", "--bundle", bundle)

    def test_resolved_child_destinations_cannot_overwrite_inputs(self):
        for name in SCRIPTS:
            children = ("source-metadata.json", "store", "store/snapshots", "releases") if "snapshot" in name else ("orrery.sqlite3",)
            for index, child in enumerate(children):
                work = self.directory / (name + str(index))
                (work / child).parent.mkdir(parents=True, exist_ok=True)
                target = self.sources / "MPCORB.DAT.gz" if child.endswith(".json") or child.endswith(".sqlite3") else self.sources
                if "database" in name:
                    target = Path(self.snapshot["path"]) / "master.jsonl.gz"
                (work / child).symlink_to(target, target_is_directory=target.is_dir())
                before = tree(work)
                self.assert_rejected(name, work, self.directory / "report.json")
                self.assertEqual(tree(work), before)

    def test_reports_cannot_replace_writer_files_or_work_ancestors(self):
        for name in SCRIPTS:
            work = self.directory / name / "work"
            paths = [work, work.parent]
            paths += [work / path for path in (("source-metadata.json", "store/current.json", "releases/latest.json", ".lock")
                      if "snapshot" in name else ("orrery.sqlite3", ".lock"))]
            for path in paths:
                with self.subTest(script=name, report=path):
                    self.assert_rejected(name, work, path)
                    self.assertFalse(work.exists())

    def test_portable_input_aliases_and_external_input_files_are_protected(self):
        for name in SCRIPTS:
            root = self.sources if "snapshot" in name else self.store
            for report in (False, True):
                alias = root.with_name(root.name.upper()) / "future-output"
                with self.subTest(script=name, alias=alias, report=report):
                    self.assert_rejected(name, self.directory / "work" if report else alias,
                                         alias if report else self.directory / "report.json")
            linked = root / ("MPCORB.headers" if "snapshot" in name else "current.json")
            outside = self.directory / (name + "-outside-input")
            linked.rename(outside)
            linked.symlink_to(outside)
            before = outside.read_bytes()
            self.assert_rejected(name, self.directory / "work", outside)
            self.assertEqual(outside.read_bytes(), before)
            renamed = root.with_name("caf\u00e9-" + root.name)
            root.rename(renamed)
            if "snapshot" in name:
                self.sources = renamed
            else:
                self.store = renamed
            normalized_alias = renamed.with_name(unicodedata.normalize("NFD", renamed.name)) / "future-output"
            self.assert_rejected(name, normalized_alias, self.directory / "report.json")
            self.assertFalse((self.directory / "work").exists())

    def test_optimized_python_rejects_before_inputs_or_outputs(self):
        for name in SCRIPTS:
            for flags, optimization in ((["-O"], "0"), (["-OO"], "0"), ([], "1"), ([], "2")):
                work, report = self.directory / "optimized-work", self.directory / "optimized-report.json"
                report.write_text("previous evidence")
                result = self.invoke(name, work, report, flags=flags, optimization=optimization)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("requires assertions", result.stderr)
                self.assertEqual(result.stdout, "")
                self.assertFalse(work.exists())
                self.assertEqual(report.read_text(), "previous evidence")

    def test_complete_runs_allow_report_inside_work_and_keep_inputs(self):
        before = {root: tree(root) for root in (self.sources, self.store, self.references)}
        for name in SCRIPTS:
            work = self.directory / name / "work"
            report = work / "report.json"
            for _ in range(2):
                result = self.invoke(name, work, report)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertEqual(json.loads(report.read_text())["status"], "passed")
                self.assertEqual(list(work.glob(".pointer-*")), [])
                for root, expected in before.items():
                    self.assertEqual(tree(root), expected)

    def test_report_failures_keep_previous_evidence_and_allow_retry(self):
        for name in SCRIPTS:
            for fault in ("write", "fsync", "replace"):
                for existing in (False, True):
                    with self.subTest(script=name, fault=fault, existing=existing):
                        case = self.directory / f"{name}-{fault}-{existing}"
                        case.mkdir()
                        report, work = case / "report.json", case / "work"
                        if existing:
                            report.write_text("previous evidence")
                        before = {root: tree(root) for root in (self.sources, self.store, self.references)}
                        failed = self.invoke(name, work, report, fault=fault)
                        self.assertNotEqual(failed.returncode, 0)
                        self.assertIn(f"injected report {fault} failure", failed.stderr)
                        if existing:
                            self.assertEqual(report.read_text(), "previous evidence")
                        else:
                            self.assertFalse(report.exists())
                        self.assertEqual(list(case.glob(".pointer-*")), [])
                        for root, expected in before.items():
                            self.assertEqual(tree(root), expected)
                        passed = self.invoke(name, work, report)
                        self.assertEqual(passed.returncode, 0, passed.stdout + passed.stderr)
                        self.assertEqual(json.loads(report.read_text())["status"], "passed")


if __name__ == "__main__":
    unittest.main()
