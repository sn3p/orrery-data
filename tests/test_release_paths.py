"""Writer placement, owned validation copies and workflow isolation boundaries."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import unittest

import test_releases
import test_saved_release


ROOT = Path(__file__).resolve().parents[1]


def tree(root):
    return {str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*") if p.is_file()}


class SavedReleasePaths(unittest.TestCase):
    setUp = test_saved_release.SavedReleaseValidation.setUp
    cli = test_saved_release.SavedReleaseValidation.cli
    producer = test_saved_release.SavedReleaseValidation.producer
    invocation = test_saved_release.SavedReleaseValidation.invocation

    def test_retained_inputs_and_producer_cannot_be_reports_or_work_directories(self):
        producer = self.producer(stop_before_prepare=False)
        snapshot = self.store / "snapshots" / self.snapshot["snapshot_version"]
        reference = next(self.references.glob("*/manifest.json"))
        alias = self.directory / "source-alias"
        alias.symlink_to(self.store, target_is_directory=True)
        cases = [(self.directory / "work", path) for path in (
            snapshot / "snapshot.json", reference, alias / "current.json",
            producer / "orrery_data/releases.py", producer / "scripts/validate_saved_database.py")]
        cases += [(path, self.directory / "report.json") for path in (
            self.store, snapshot / "new", self.references / "new", alias / "new",
            self.directory, producer / "scripts/work")]
        before = {root: tree(root) for root in (self.store, self.references, producer)}
        for work, report in cases:
            with self.subTest(work=work, report=report):
                result = subprocess.run(self.invocation(producer, work, report), cwd=ROOT,
                                        capture_output=True, text=True, timeout=30)
                self.assertNotEqual(result.returncode, 0, result.stdout)
                self.assertIn("overlap", result.stderr)
                self.assertNotIn("Preparing", result.stdout)
                for root, expected in before.items():
                    self.assertEqual(tree(root), expected)
        self.assertFalse((self.directory / "work").exists())
        self.assertFalse((self.directory / "report.json").exists())

    def test_complete_validation_owns_its_transfer_and_allows_report_in_work(self):
        producer = self.producer(stop_before_prepare=False)
        work = self.directory / "work"
        old_copy = work / "downloaded-copy"
        old_copy.mkdir(parents=True)
        (old_copy / "keep").write_text("unrelated retained evidence")
        report = work / "report.json"
        inputs = {root: tree(root) for root in (self.store, self.references)}
        for _ in range(2):
            result = subprocess.run(self.invocation(producer, work, report), cwd=ROOT,
                                    capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(json.loads(report.read_text())["status"], "passed")
            self.assertEqual((old_copy / "keep").read_text(), "unrelated retained evidence")
            self.assertEqual(list(work.glob(".transfer-*")), [])
            for root, expected in inputs.items():
                self.assertEqual(tree(root), expected)


class ReleaseWriterPaths(unittest.TestCase):
    setUpClass = classmethod(test_releases.ReleaseCLI.setUpClass.__func__)
    tearDownClass = classmethod(test_releases.ReleaseCLI.tearDownClass.__func__)
    setUp = test_releases.ReleaseCLI.setUp
    cli = test_releases.ReleaseCLI.cli
    prepare = test_releases.ReleaseCLI.prepare
    http_args = test_releases.ReleaseCLI.http_args

    def test_all_writer_entries_preserve_existing_candidates_and_aliases(self):
        prepared = self.prepare()
        bundle = Path(prepared["path"])
        renamed = self.directory / "renamed-bundle"
        shutil.copytree(bundle, renamed)
        snapshot = self.store / "snapshots" / prepared["snapshot_version"]
        roots = (bundle, renamed, snapshot, bundle / "exports/full")
        for index, root in enumerate(roots):
            alias = self.directory / f"alias-{index}"
            alias.symlink_to(root, target_is_directory=True)
            before = tree(root)
            for target in (root, root / "new/deep", alias / "new"):
                commands = [
                    ("refresh", "--store", target, *self.http_args()),
                    ("export", "--store", self.store, "--output", target),
                    ("build-db", "--store", self.store, "--database", target / "data.sqlite3"),
                    ("prepare-release", "--store", self.store, "--output", target,
                     "--snapshot", prepared["snapshot_version"], "--producer-commit", test_releases.COMMIT),
                    ("prepare-release", "--store", target, "--output", self.directory / "new-output",
                     "--producer-commit", test_releases.COMMIT, *self.http_args()),
                ]
                for command in commands:
                    with self.subTest(target=target, command=command[0]):
                        self.requests.clear()
                        error = self.cli(*command, code=1)["error"]
                        self.assertTrue("immutable" in error or "candidate" in error, error)
                        self.assertEqual(self.requests, [])
                        self.assertEqual(tree(root), before)

    def workflow_environment(self, baseline):
        return {**os.environ, "RELEASE_PRODUCER_COMMIT": test_releases.COMMIT,
                "RELEASE_BASELINE_COUNTS": json.dumps(baseline), "RELEASE_SELECTED_LIMITS": "2",
                "GITHUB_OUTPUT": "", "GITHUB_STEP_SUMMARY": ""}

    def test_workflow_rejects_immutable_work_and_metadata_paths_before_writes(self):
        prepared = self.prepare()
        bundle = Path(prepared["path"])
        before = tree(bundle)
        env = self.workflow_environment(prepared["counts"])
        helper = ROOT / "scripts/prepare_release_workflow.py"
        alias = self.directory / "workflow-alias"
        alias.symlink_to(bundle, target_is_directory=True)
        for work in (bundle, bundle / "new", alias / "new"):
            result = subprocess.run([sys.executable, str(helper), "--work-dir", str(work)],
                                    cwd=ROOT, env=env, capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
            self.assertEqual(tree(bundle), before)
        work = self.directory / "workflow"
        for key in ("GITHUB_OUTPUT", "GITHUB_STEP_SUMMARY"):
            result = subprocess.run([sys.executable, str(helper), "--work-dir", str(work)], cwd=ROOT,
                                    env={**env, key: str(bundle / "NOTICE.txt")},
                                    capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
            self.assertFalse(work.exists())
            self.assertEqual(tree(bundle), before)

    def test_concurrent_workflow_cannot_replace_the_active_baseline(self):
        helper = ROOT / "scripts/prepare_release_workflow.py"
        work = self.directory / "workflow"
        paused, resume = self.directory / "paused", self.directory / "resume"
        # Pause after the helper publishes its baseline but before the actual
        # producer process reads it. The second helper must fail before writing.
        script = """import runpy, subprocess, sys, time
from pathlib import Path
from unittest.mock import patch
original = subprocess.run
helper, paused, resume = sys.argv[1:4]
sys.argv = [helper, *sys.argv[4:]]
def gated(command, *args, **kwargs):
    if 'prepare-release' in command:
        Path(paused).touch()
        deadline = time.monotonic() + 20
        while not Path(resume).exists():
            if time.monotonic() > deadline: raise RuntimeError('gate timed out')
            time.sleep(.01)
    return original(command, *args, **kwargs)
with patch('subprocess.run', gated): runpy.run_path(helper, run_name='__main__')
"""
        strict = {"orbital_records": 10, "master_records": 10, "discovery_records": 7, "known_discovery": 7}
        normal = {"orbital_records": 9, "master_records": 9, "discovery_records": 6, "known_discovery": 6}
        options = ["--work-dir", str(work), *self.http_args()]
        first = subprocess.Popen([sys.executable, "-c", script, str(helper), str(paused), str(resume), *options],
                                 cwd=ROOT, env=self.workflow_environment(strict),
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            deadline = time.monotonic() + 15
            while not paused.exists() and first.poll() is None and time.monotonic() < deadline:
                time.sleep(.01)
            self.assertTrue(paused.exists(), "first workflow did not reach baseline read boundary")
            second = subprocess.run([sys.executable, str(helper), *options], cwd=ROOT,
                                    env=self.workflow_environment(normal), capture_output=True, text=True, timeout=30)
            self.assertEqual(second.returncode, 1, second.stdout + second.stderr)
            self.assertIn("Another writer", second.stderr)
            self.assertEqual(json.loads((work / "baseline-counts.json").read_text()), strict)
            resume.touch()
            stdout, stderr = first.communicate(timeout=30)
            self.assertEqual(first.returncode, 1, stdout + stderr)
            self.assertIn("decreased", stderr)
            self.assertFalse((work / "releases/latest.json").exists())
            retried = subprocess.run([sys.executable, str(helper), *options], cwd=ROOT,
                                     env=self.workflow_environment(normal), capture_output=True, text=True, timeout=30)
            self.assertEqual(retried.returncode, 0, retried.stdout + retried.stderr)
        finally:
            if first.poll() is None:
                first.kill()
                first.communicate(timeout=10)

    def test_workflow_metadata_replacement_preserves_external_link_targets(self):
        work = self.directory / "workflow"
        work.mkdir()
        outside = self.directory / "retained.json"
        outside.write_text("retained external evidence")
        (work / "baseline-counts.json").symlink_to(outside)
        os.link(outside, work / "prepared-release.json")
        baseline = {"orbital_records": 9, "master_records": 9, "discovery_records": 6, "known_discovery": 6}
        result = subprocess.run([sys.executable, str(ROOT / "scripts/prepare_release_workflow.py"),
                                 "--work-dir", str(work), *self.http_args()], cwd=ROOT,
                                env=self.workflow_environment(baseline), capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(outside.read_text(), "retained external evidence")
        self.assertFalse((work / "baseline-counts.json").is_symlink())
        self.assertEqual(json.loads((work / "baseline-counts.json").read_text()), baseline)
        self.assertEqual(json.loads((work / "prepared-release.json").read_text())["status"], "verified")
