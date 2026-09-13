"""Saved-release validation must pin its source/code and run its checks."""

import ast
import hashlib
import json
import os
from pathlib import Path
import runpy
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]

# Inject I/O failures only at the final report boundary in the committed worker.
# Both the old direct write and temporary-file publication use io.open.
REPORT_IO_HOOK = '''
import io
import os
report_target = Path(os.environ["ORRERY_TEST_REPORT"]).resolve()
report_fault = os.environ.get("ORRERY_TEST_REPORT_FAULT")
report_fds = set()
real_open, real_fsync, real_replace = io.open, os.fsync, os.replace
class ReportStream:
    def __init__(self, stream): self.stream = stream
    def __getattr__(self, name): return getattr(self.stream, name)
    def __enter__(self): return self
    def __exit__(self, *args): return self.stream.__exit__(*args)
    def write(self, payload):
        if report_fault == "write":
            self.stream.write(payload[:20])
            self.stream.flush()
            raise OSError("injected report write failure")
        return self.stream.write(payload)
    def flush(self):
        if report_fault == "flush": raise OSError("injected report flush failure")
        return self.stream.flush()
def report_open(file, *args, **kwargs):
    stream = real_open(file, *args, **kwargs)
    if isinstance(file, (str, bytes, os.PathLike)) and Path(file) in (report_target, report_target.parent):
        report_fds.add(stream.fileno())
        return ReportStream(stream)
    return stream
def report_fsync(fd):
    if fd in report_fds and report_fault == "fsync": raise OSError("injected report fsync failure")
    return real_fsync(fd)
def report_replace(source, target):
    if Path(target) == report_target and report_fault == "replace":
        raise OSError("injected report replace failure")
    return real_replace(source, target)
io.open, os.fsync, os.replace = report_open, report_fsync, report_replace
'''


class SavedReleaseValidation(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.store = self.directory / "store"
        self.references = self.directory / "references"
        # Small valid inputs differ from the retained full dataset. Only the
        # validation tests substitute fixture identities; optimization tests
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

    def producer(self, stop_before_prepare=True, pause=False, report_io=False):
        # Commit the real producer/validator with only retained fixture values
        # substituted. The bootstrap must read these committed Git objects.
        producer = self.directory / "producer"
        if not producer.exists():
            shutil.copytree(ROOT / "orrery_data", producer / "orrery_data", ignore=shutil.ignore_patterns("__pycache__"))
            (producer / "scripts").mkdir()
            shutil.copyfile(ROOT / "pyproject.toml", producer / "pyproject.toml")
            version = self.snapshot["snapshot_version"]
            master = self.store / "snapshots" / version / "master.jsonl.gz"
            values = {"EXPECTED_SNAPSHOT_VERSION": version,
                      "EXPECTED_COUNTS": self.snapshot["counts"],
                      "MASTER_SHA256": hashlib.sha256(master.read_bytes()).hexdigest(),
                      "EXPECTED": {key: self.snapshot["counts"][key]
                                   for key in ("master_records", "known_discovery", "missing_discovery")}}
            for name in ("validate_saved_release.py", "validate_saved_database.py"):
                source = (ROOT / "scripts" / name).read_text()
                lines = source.splitlines(keepends=True)
                for node in reversed(ast.parse(source).body):
                    if (isinstance(node, ast.Assign) and len(node.targets) == 1
                            and isinstance(node.targets[0], ast.Name) and node.targets[0].id in values):
                        key = node.targets[0].id
                        lines[node.lineno - 1:node.end_lineno] = [f"{key} = {values[key]!r}\n"]
                source = "".join(lines)
                if name == "validate_saved_release.py":
                    if report_io:
                        source = source.replace("def arguments():", REPORT_IO_HOOK + "\ndef arguments():")
                    if stop_before_prepare:
                        source = source.replace('    prepared, timings["prepare"] = cli(',
                                                "    raise SystemExit('release preparation reached')\n"
                                                '    prepared, timings["prepare"] = cli(')
                    else:
                        source = source.replace('(("all", 1563495), ("known", 895910), ("missing", 667585))',
                                                '(("all", 9), ("known", 6), ("missing", 3))')
                        source = source.replace('"K17S44L"', '"J60S01B"').replace('== 360.0', '== 224.49081')
                (producer / "scripts" / name).write_text(source)
            if pause:
                entrypoint = producer / "orrery_data/__main__.py"
                hook = ("import json, os, time\nfrom pathlib import Path\nimport sys\n"
                        "events = Path(os.environ['ORRERY_TEST_EVENTS'])\n"
                        "previous = events.read_text().splitlines() if events.exists() else []\n"
                        "with events.open('a') as stream:\n"
                        " stream.write(json.dumps({'root': str(Path(__file__).resolve().parents[1]), "
                        "'command': sys.argv[1]}) + '\\n')\n"
                        "gate = ('repeat' if sys.argv[1] == 'prepare-release' and previous else "
                        "'copy' if sys.argv[1] == 'verify-release' and len(previous) == 2 else None)\n"
                        "if gate:\n"
                        " events.with_name(gate + '-paused').touch()\n"
                        " deadline = time.monotonic() + 20\n"
                        " while not events.with_name(gate + '-resume').exists():\n"
                        "  if time.monotonic() > deadline: raise SystemExit('test gate timed out')\n"
                        "  time.sleep(0.02)\n")
                entrypoint.write_text(hook + entrypoint.read_text())
            subprocess.run(["git", "init", "--template=", "--quiet", str(producer)], check=True, capture_output=True)
            subprocess.run(["git", "config", "core.hooksPath", os.devnull], cwd=producer, check=True, capture_output=True)
            subprocess.run(["git", "add", "."], cwd=producer, check=True, capture_output=True)
            subprocess.run(["git", "-c", "user.name=Test", "-c", "user.email=test@example.invalid",
                            "-c", "commit.gpgsign=false", "commit", "--quiet", "-m", "Fixture"],
                           cwd=producer, check=True, capture_output=True)
        return producer

    def invocation(self, producer, work, report):
        return [sys.executable, str(producer / "scripts/validate_saved_release.py"), "--store", str(self.store),
                "--reference-exports", str(self.references), "--work-dir", str(work), "--report", str(report)]

    def preflight(self, work, report):
        # Run the real argument/file/preflight and committed-snapshot boundary,
        # stopping only at release preparation; no identity check is mocked.
        return subprocess.run(self.invocation(self.producer(), work, report),
                              cwd=ROOT, env={**os.environ, "PYTHONOPTIMIZE": "0"},
                              capture_output=True, text=True, timeout=30)

    def test_committed_producer_survives_changed_and_restored_source_and_head(self):
        producer = self.producer(stop_before_prepare=False, pause=True)
        commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=producer, text=True).strip()
        work, report, events = self.directory / "work", self.directory / "report.json", self.directory / "events"
        process = subprocess.Popen(self.invocation(producer, work, report), cwd=ROOT,
                                   env={**os.environ, "PYTHONOPTIMIZE": "0", "ORRERY_TEST_EVENTS": str(events)},
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            def wait_for(name):
                deadline = time.monotonic() + 20
                while not events.with_name(name).exists() and process.poll() is None and time.monotonic() < deadline:
                    time.sleep(0.02)
                self.assertTrue(events.with_name(name).exists(), f"validator did not reach {name}")

            wait_for("repeat-paused")
            # Changed producer code must never execute, even when HEAD moves
            # with it and both changes disappear before the final report.
            for name in ("orrery_data/releases.py", "scripts/validate_saved_database.py"):
                (producer / name).write_text("raise RuntimeError('changed checkout executed')\n")
            subprocess.run(["git", "add", "."], cwd=producer, check=True, capture_output=True)
            subprocess.run(["git", "-c", "user.name=Test", "-c", "user.email=test@example.invalid",
                            "-c", "commit.gpgsign=false", "commit", "--quiet", "-m", "Concurrent changes"],
                           cwd=producer, check=True, capture_output=True)
            self.assertNotEqual(subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=producer, text=True).strip(), commit)
            events.with_name("repeat-resume").touch()
            wait_for("copy-paused")
            subprocess.run(["git", "reset", "--hard", commit], cwd=producer, check=True, capture_output=True)
            events.with_name("copy-resume").touch()
            stdout, stderr = process.communicate(timeout=30)
            self.assertEqual(process.returncode, 0, stdout + stderr)
        finally:
            if process.poll() is None:
                process.kill()
                process.communicate(timeout=10)
        result = json.loads(report.read_text())
        self.assertEqual(result["status"], "passed")
        self.assertEqual(result["producer_commit"], commit)
        self.assertEqual(result["release"]["producer"]["commit"], commit)
        calls = [json.loads(line) for line in events.read_text().splitlines()]
        self.assertEqual([call["command"] for call in calls],
                         ["prepare-release", "prepare-release", "verify-release", "query", "query", "query", "query", "verify-release"])
        roots = {call["root"] for call in calls}
        self.assertEqual(len(roots), 1)
        self.assertNotIn(str(producer), roots)
        self.assertFalse(Path(roots.pop()).exists(), "private producer snapshot should be removed")

    def test_report_publication_failures_preserve_evidence_and_allow_retry(self):
        producer = self.producer(stop_before_prepare=False, report_io=True)
        previous = b'{"previous_evidence":"keep"}\n'
        for existing in (False, True):
            for fault in ("write", "flush", "fsync", "replace"):
                with self.subTest(existing=existing, fault=fault):
                    root = self.directory / f"report-{existing}-{fault}"
                    root.mkdir()
                    report = root / "report.json"
                    if existing:
                        report.write_bytes(previous)
                    work = self.directory / f"work-{existing}-{fault}"
                    env = {**os.environ, "PYTHONOPTIMIZE": "0", "ORRERY_TEST_REPORT": str(report),
                           "ORRERY_TEST_REPORT_FAULT": fault}
                    failed = subprocess.run(self.invocation(producer, work, report), cwd=ROOT,
                                            env=env, capture_output=True, text=True, timeout=30)
                    self.assertNotEqual(failed.returncode, 0, failed.stdout)
                    self.assertIn(f"injected report {fault} failure", failed.stderr)
                    self.assertNotIn('"status": "passed"', failed.stdout)
                    self.assertEqual(list(root.iterdir()), [report] if existing else [])
                    if existing:
                        self.assertEqual(report.read_bytes(), previous)
                    env.pop("ORRERY_TEST_REPORT_FAULT")
                    passed = subprocess.run(self.invocation(producer, work, report), cwd=ROOT,
                                            env=env, capture_output=True, text=True, timeout=30)
                    self.assertEqual(passed.returncode, 0, passed.stdout + passed.stderr)
                    result = json.loads(report.read_text())
                    self.assertEqual(result["status"], "passed")
                    self.assertEqual(report.read_text(), json.dumps(result, indent=2) + "\n")
                    self.assertEqual(list(root.iterdir()), [report])

    def replacement_producer(self, kind, dirty=False):
        producer = self.producer(stop_before_prepare=False)
        def git(*args):
            return subprocess.check_output(["git", *args], cwd=producer, text=True).strip()
        commit = git("rev-parse", "HEAD")
        helper = "scripts/validate_saved_database.py"
        marker = self.directory / "replacement-executed"
        path = producer / helper
        path.write_text(path.read_text() + f"\nPath({str(marker)!r}).touch()\n")
        git("add", helper)
        git("-c", "user.name=Test", "-c", "user.email=test@example.invalid",
            "-c", "commit.gpgsign=false", "commit", "--quiet", "-m", "Replacement producer")
        replacement = git("rev-parse", "HEAD")
        git("reset", "--soft" if dirty else "--hard", commit)
        suffix = {"commit": "", "tree": "^{tree}", "blob": ":" + helper}[kind]
        git("replace", git("rev-parse", commit + suffix), git("rev-parse", replacement + suffix))
        return producer, commit, marker

    def test_commit_replacement_cannot_hide_a_different_producer_checkout(self):
        producer, _, marker = self.replacement_producer("commit", dirty=True)
        # Default Git sees a clean tree even though HEAD names different code.
        subprocess.run(["git", "diff", "--exit-code", "HEAD"], cwd=producer, check=True, capture_output=True)
        work, report = self.directory / "work", self.directory / "report.json"
        report.write_text("previous evidence")
        result = subprocess.run(self.invocation(producer, work, report), cwd=ROOT,
                                capture_output=True, text=True, timeout=30)
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertFalse(marker.exists(), "replacement producer must not execute")
        self.assertFalse(work.exists())
        self.assertEqual(report.read_text(), "previous evidence")

    def test_raw_commit_tree_and_blob_are_used_despite_replacement_refs(self):
        for kind in ("commit", "tree", "blob"):
            with self.subTest(kind=kind):
                producer, commit, marker = self.replacement_producer(kind)
                report = self.directory / f"report-{kind}.json"
                result = subprocess.run(self.invocation(producer, self.directory / f"work-{kind}", report),
                                        cwd=ROOT, capture_output=True, text=True, timeout=30)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertFalse(marker.exists(), "replacement blob must not execute")
                saved = json.loads(report.read_text())
                self.assertEqual(saved["producer_commit"], commit)
                self.assertEqual(saved["release"]["producer"]["commit"], commit)
            shutil.rmtree(producer)

    def test_all_retained_counts_are_pinned_before_preparation(self):
        self.producer()
        path = self.store / "snapshots" / self.snapshot["snapshot_version"] / "snapshot.json"
        original = json.loads(path.read_text())
        # Each mutation still satisfies the schema's arithmetic invariants.
        changes = [
            {"numbered_orbits": -1, "unnumbered_orbits": 1},
            {"orbital_records": 1, "unsupported_orbits": 1, "unnumbered_orbits": 1},
            {"discovery_records": 1, "unmatched_discovery_records": 1},
            {"master_records": -1, "unsupported_orbits": 1, "missing_discovery": -1},
            {"known_discovery": -1, "missing_discovery": 1, "unmatched_discovery_records": 1},
        ]
        from orrery_data.pipeline import validate_snapshot_manifest
        for index, delta in enumerate(changes):
            with self.subTest(changes=delta):
                snapshot = json.loads(json.dumps(original))
                for key, difference in delta.items():
                    snapshot["counts"][key] += difference
                snapshot["exclusions"]["master"]["non_elliptic_orbits"] = snapshot["counts"]["unsupported_orbits"]
                snapshot["exclusions"]["discovery"]["missing_discovery_date"] = snapshot["counts"]["missing_discovery"]
                validate_snapshot_manifest(snapshot, self.snapshot["snapshot_version"])
                path.write_text(json.dumps(snapshot))
                before = {str(p): p.read_bytes() for p in self.store.rglob("*") if p.is_file()}
                report, work = self.directory / f"counts-{index}.json", self.directory / f"counts-work-{index}"
                report.write_text("previous evidence")
                result = self.preflight(work, report)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("retained validated snapshot counts", result.stderr)
                self.assertNotIn("Traceback", result.stderr)
                self.assertFalse(result.stdout)
                self.assertFalse(work.exists())
                self.assertEqual(report.read_text(), "previous evidence")
                self.assertEqual({str(p): p.read_bytes() for p in self.store.rglob("*") if p.is_file()}, before)
        path.write_text(json.dumps(original))
        result = self.preflight(self.directory / "valid-counts", self.directory / "valid-counts.json")
        self.assertIn("release preparation reached", result.stderr)

    def test_retained_pins_match_committed_full_data_evidence(self):
        validator = runpy.run_path(str(ROOT / "scripts/validate_saved_release.py"))
        evidence = json.loads((ROOT / "docs/release-validation-result.json").read_text())["release"]
        self.assertEqual(validator["EXPECTED_SNAPSHOT_VERSION"], evidence["snapshot_version"])
        self.assertEqual(validator["EXPECTED_COUNTS"], evidence["counts"])

    def test_missing_retained_snapshot_does_not_fall_back_to_identical_current_master(self):
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
        self.producer()
        shutil.rmtree(self.store / "snapshots" / self.snapshot["snapshot_version"])
        before = {str(p): p.read_bytes() for p in self.store.rglob("*") if p.is_file()}
        for existing in (False, True):
            with self.subTest(existing_report=existing):
                work, report = self.directory / f"work-{existing}", self.directory / f"report-{existing}.json"
                previous = b'{"previous_evidence":"keep"}\n'
                if existing:
                    report.write_bytes(previous)
                result = self.preflight(work, report)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("Requires retained validated 2026-09-12 source snapshot", result.stderr)
                self.assertNotIn("Traceback", result.stderr)
                self.assertFalse(result.stdout)
                self.assertFalse(work.exists())
                if existing:
                    self.assertEqual(report.read_bytes(), previous)
                else:
                    self.assertFalse(report.exists())
        self.assertEqual({str(p): p.read_bytes() for p in self.store.rglob("*") if p.is_file()}, before)

    def test_retained_snapshot_is_independent_of_current_pointer(self):
        producer = self.producer(stop_before_prepare=False)
        current = self.store / "current.json"
        for index, value in enumerate((None, "not json", json.dumps({"snapshot_version": "snapshot-v1-" + "f" * 64}))):
            with self.subTest(current=value):
                current.unlink(missing_ok=True)
                if value is not None:
                    current.write_text(value)
                report = self.directory / f"report-{index}.json"
                result = subprocess.run(self.invocation(producer, self.directory / f"work-{index}", report),
                                        cwd=ROOT, capture_output=True, text=True, timeout=30)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertEqual(json.loads(report.read_text())["release"]["snapshot_version"], self.snapshot["snapshot_version"])

    def test_reference_preflight_rejects_missing_malformed_and_conflicting_exports(self):
        producer = self.producer()
        original = self.references
        for case in ("missing-full", "missing-selected", "list", "selection", "artifact", "profile", "records", "header-fields", "conflict"):
            with self.subTest(case=case):
                refs = self.directory / f"refs-{case}"
                shutil.copytree(original, refs)
                paths = list(refs.glob("*/manifest.json"))
                full = next(p for p in paths if json.loads(p.read_text())["selection"]["limit"] is None)
                selected = next(p for p in paths if p != full)
                if case.startswith("missing"):
                    shutil.rmtree((full if case == "missing-full" else selected).parent)
                else:
                    value = json.loads(full.read_text())
                    if case == "list":
                        value = []
                    elif case == "selection":
                        del value["selection"]
                    elif case == "artifact":
                        del value["artifacts"]["catalog.json"]["bytes"]
                    elif case == "profile":
                        value["artifacts"]["catalog.json"]["profile"] = []
                    elif case == "records":
                        value["artifacts"]["catalog.json"]["records"] += 1
                    elif case == "header-fields":
                        value["artifacts"]["MPCORB-header.txt"]["extra"] = 1
                    else:
                        (refs / "conflict").mkdir()
                        full = refs / "conflict/manifest.json"
                        value["artifacts"]["catalog.json"]["sha256"] = "f" * 64
                    full.write_text(json.dumps(value))
                report, work = self.directory / f"{case}.json", self.directory / f"work-{case}"
                report.write_text("previous report")
                invocation = self.invocation(producer, work, report) + ["--reference-exports", str(refs)]
                result = subprocess.run(invocation, cwd=ROOT, capture_output=True, text=True, timeout=30)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("reference export", result.stderr.lower())
                self.assertNotIn("Traceback", result.stderr)
                self.assertFalse(result.stdout)
                self.assertFalse(work.exists())
                self.assertEqual(report.read_text(), "previous report")
        # Identical historical reference payloads are interchangeable.
        shutil.copytree(next(original.glob("*/manifest.json")).parent, original / "identical-copy")
        result = self.preflight(self.directory / "valid-work", self.directory / "valid-report.json")
        self.assertIn("release preparation reached", result.stderr)

    def test_fixture_ignores_global_git_hooks_and_templates(self):
        hooks, templates = self.directory / "hooks", self.directory / "templates"
        hooks.mkdir()
        (templates / "hooks").mkdir(parents=True)
        marker = self.directory / "hook-ran"
        for directory in (hooks, templates / "hooks"):
            hook = directory / "pre-commit"
            hook.write_text(f"#!/bin/sh\ntouch '{marker}'\nexit 1\n")
            hook.chmod(0o755)
        config = self.directory / "global-gitconfig"
        config.write_text(f'[core]\n hooksPath = "{hooks}"\n[init]\n templateDir = "{templates}"\n')
        with patch.dict(os.environ, {"GIT_CONFIG_GLOBAL": str(config)}):
            self.producer()
        self.assertFalse(marker.exists())

    def test_retained_source_reaches_release_preparation(self):
        work, report = self.directory / "work", self.directory / "report.json"
        result = self.preflight(work, report)
        self.assertIn("release preparation reached", result.stderr)
        self.assertTrue(work.is_dir())
        self.assertFalse(report.exists())

    def test_relative_paths_keep_the_callers_directory(self):
        producer = self.producer()
        result = subprocess.run([sys.executable, str(producer / "scripts/validate_saved_release.py"),
                                 "--store", "store", "--reference-exports", "references", "--work-dir", "work",
                                 "--report", "report.json"], cwd=self.directory,
                                env={**os.environ, "PYTHONOPTIMIZE": "0"}, capture_output=True, text=True, timeout=30)
        self.assertIn("release preparation reached", result.stderr)
        self.assertTrue((self.directory / "work").is_dir())
        self.assertFalse((self.directory / "report.json").exists())

    def test_dirty_producer_preserves_existing_report_before_preparation(self):
        producer = self.producer()
        path = producer / "orrery_data/releases.py"
        path.write_text(path.read_text() + "\n# Uncommitted producer change\n")
        work, report = self.directory / "work", self.directory / "report.json"
        previous = b'{"previous_evidence":"keep"}\n'
        report.write_bytes(previous)
        result = self.preflight(work, report)
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("release preparation reached", result.stderr)
        self.assertFalse(work.exists())
        self.assertEqual(report.read_bytes(), previous)

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
