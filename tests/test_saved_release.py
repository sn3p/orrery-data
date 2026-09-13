"""Saved-release validation must pin its source/code and run its checks."""

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

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
                      "MASTER_SHA256": hashlib.sha256(master.read_bytes()).hexdigest(),
                      "EXPECTED": {key: self.snapshot["counts"][key]
                                   for key in ("master_records", "known_discovery", "missing_discovery")}}
            for name in ("validate_saved_release.py", "validate_saved_database.py"):
                source = (ROOT / "scripts" / name).read_text()
                for key, value in values.items():
                    source = "\n".join(f"{key} = {value!r}" if line.startswith(key + " = ") else line
                                       for line in source.split("\n"))
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
            subprocess.run(["git", "init", "--quiet", str(producer)], check=True, capture_output=True)
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
