"""Scheduled acquisition commit boundaries and workflow policy."""

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from scripts import prepare_scheduled_refresh as refresh_helper


ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "scripts/prepare_scheduled_refresh.py"
WORKFLOW = ROOT / ".github/workflows/mpc-refresh.yml"


class RefreshWorkflow(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        metadata = tempfile.TemporaryDirectory()
        self.addCleanup(metadata.cleanup)
        self.metadata = Path(metadata.name)
        subprocess.run(["git", "init", "--quiet"], cwd=self.root, check=True)
        subprocess.run(["git", "config", "user.name", "Test"], cwd=self.root, check=True)
        subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=self.root, check=True)
        (self.root / ".gitignore").write_text(".context/\n", encoding="utf-8")
        self.committed_pin = self.write_data("a" * 64, "2026-09-12T00:00:00Z")
        self.commit()

    def write_data(self, marker, retrieved_at):
        data = self.root / "data"
        data.mkdir(exist_ok=True)
        for old in data.glob("index-*.json"):
            old.unlink()
        index = {
            "snapshot_version": "snapshot-v1-" + marker,
            "catalog_id": "export-v1-" + marker,
            "counts": {"orbital_records": 3, "master_records": 3, "discovery_records": 2,
                       "known_discovery": 2, "missing_discovery": 1},
            "sources": {
                "mpcorb": {"retrieved_at": retrieved_at, "last_modified": "Friday"},
                "numbered": {"retrieved_at": retrieved_at, "last_modified": "Tuesday"},
            },
        }
        encoded = json.dumps(index).encode()
        digest = hashlib.sha256(encoded).hexdigest()
        name = f"index-{digest}.json"
        (data / name).write_bytes(encoded)
        pin = {"url": name, "sha256": digest, "bytes": len(encoded)}
        (data / "latest.json").write_text(json.dumps({"index": pin}), encoding="utf-8")
        return pin

    def commit(self):
        subprocess.run(["git", "add", "-A"], cwd=self.root, check=True)
        subprocess.run(["git", "commit", "--quiet", "-m", "data"], cwd=self.root, check=True)

    def replace_index(self, pin, value):
        index = self.root / "data" / pin["url"]
        encoded = json.dumps(value).encode()
        digest = hashlib.sha256(encoded).hexdigest()
        replacement = index.with_name(f"index-{digest}.json")
        index.unlink()
        replacement.write_bytes(encoded)
        updated = {"url": replacement.name, "sha256": digest, "bytes": len(encoded)}
        (self.root / "data/latest.json").write_text(
            json.dumps({"index": updated}), encoding="utf-8")
        return updated

    def results(self, pin, status="updated"):
        summary = {"path": str(self.root / "data"), "pin": pin,
                   "records": 2, "chunks": 1, "files": 2, "bytes": 100}
        refresh = {**summary, "status": status,
                   "changes": {"added": [pin["url"]] if status == "updated" else [],
                               "changed": ["latest.json"] if status == "updated" else [],
                               "unchanged": [] if status == "updated" else [pin["url"], "latest.json"],
                               "deleted": [self.committed_pin["url"]] if status == "updated" else []}}
        return refresh, summary

    def run_helper(self, refresh, verification, code=0, output_path=None, summary_path=None):
        context = self.root / ".context"
        context.mkdir(exist_ok=True)
        refresh_path, verification_path = context / "refresh.json", context / "verification.json"
        output = output_path or self.metadata / "output"
        summary = summary_path or self.metadata / "summary"
        refresh_path.write_text(json.dumps(refresh), encoding="utf-8")
        verification_path.write_text(json.dumps(verification), encoding="utf-8")
        result = subprocess.run([
            sys.executable, HELPER, "--repository", self.root,
            "--refresh-result", refresh_path, "--verification-result", verification_path,
            "--github-output", output, "--github-summary", summary,
        ], capture_output=True, text=True)
        self.assertEqual(result.returncode, code, result.stdout + result.stderr)
        return result, output, summary

    def test_changed_data_prepares_direct_commit_metadata(self):
        pin = self.write_data("b" * 64, "2026-09-18T12:34:56Z")
        refresh, verification = self.results(pin)
        result, output, summary = self.run_helper(refresh, verification)
        value = json.loads(result.stdout)
        self.assertTrue(value["changed"])
        self.assertEqual(value["title"], "Refresh MPC browser data (2026-09-18)")
        self.assertEqual(value["paths"], 3)
        self.assertIn("title=Refresh MPC browser data (2026-09-18)", output.read_text())
        self.assertIn("commit required", summary.read_text())

    def test_no_data_diff_reports_noop(self):
        pin = json.loads((self.root / "data/latest.json").read_text())["index"]
        refresh, verification = self.results(pin, status="unchanged")
        result, output, summary = self.run_helper(refresh, verification)
        self.assertFalse(json.loads(result.stdout)["changed"])
        self.assertIn("changed=false", output.read_text())
        self.assertIn("no committed data change", summary.read_text())

    def test_mismatch_or_change_outside_data_is_rejected(self):
        pin = self.write_data("b" * 64, "2026-09-18T12:34:56Z")
        refresh, verification = self.results(pin)
        verification["records"] = 1
        self.assertIn("disagree on records", self.run_helper(refresh, verification, code=1)[0].stderr)
        verification["records"] = 2
        (self.root / "README.md").write_text("unexpected", encoding="utf-8")
        self.assertIn("outside data/", self.run_helper(refresh, verification, code=1)[0].stderr)

    def test_non_utf8_unexpected_git_path_is_rejected_cleanly(self):
        outputs = iter((b"unexpected-\xff\0", b"", b"", b""))

        def git_result(args, **_kwargs):
            return subprocess.CompletedProcess(args, 0, next(outputs), b"")

        with mock.patch.object(refresh_helper.subprocess, "run", side_effect=git_result):
            with self.assertRaisesRegex(ValueError, "outside data/"):
                refresh_helper.worktree_changes(self.root)

    def test_fresh_runner_rejects_decrease_from_committed_index(self):
        pin = self.write_data("b" * 64, "2026-09-18T12:34:56Z")
        index = self.root / "data" / pin["url"]
        value = json.loads(index.read_text())
        value["counts"]["orbital_records"] = 2
        pin = self.replace_index(pin, value)
        refresh, verification = self.results(pin)
        self.assertIn("decreased from committed 3 to 2", self.run_helper(refresh, verification, code=1)[0].stderr)

    def test_required_index_count_is_validated(self):
        pin = self.write_data("b" * 64, "2026-09-18T12:34:56Z")
        index = self.root / "data" / pin["url"]
        value = json.loads(index.read_text())
        del value["counts"]["missing_discovery"]
        pin = self.replace_index(pin, value)
        refresh, verification = self.results(pin)
        result = self.run_helper(refresh, verification, code=1)[0]
        self.assertIn("missing required count missing_discovery", result.stderr)

    def test_result_documents_require_complete_typed_summary_and_exact_pin(self):
        pin = self.write_data("b" * 64, "2026-09-18T12:34:56Z")
        refresh, verification = self.results(pin)
        cases = []

        missing = json.loads(json.dumps(refresh))
        missing_verification = json.loads(json.dumps(verification))
        del missing["bytes"]
        del missing_verification["bytes"]
        cases.append((missing, missing_verification, "invalid bytes"))

        wrong_type = json.loads(json.dumps(refresh))
        wrong_type_verification = json.loads(json.dumps(verification))
        wrong_type["records"] = wrong_type_verification["records"] = True
        cases.append((wrong_type, wrong_type_verification, "invalid records"))

        wrong_pin = json.loads(json.dumps(refresh))
        wrong_pin_verification = json.loads(json.dumps(verification))
        wrong_pin["pin"]["url"] = wrong_pin_verification["pin"]["url"] = "index-" + "c" * 64 + ".json"
        cases.append((wrong_pin, wrong_pin_verification, "invalid pin"))

        wrong_pin_size = json.loads(json.dumps(refresh))
        wrong_pin_size_verification = json.loads(json.dumps(verification))
        wrong_pin_size["pin"]["bytes"] += 1
        wrong_pin_size_verification["pin"]["bytes"] += 1
        cases.append((wrong_pin_size, wrong_pin_size_verification, "does not match the browser index"))
        cases.append(([], [], "must be an object"))

        for candidate, candidate_verification, message in cases:
            with self.subTest(message=message):
                result = self.run_helper(candidate, candidate_verification, code=1)[0]
                self.assertIn(message, result.stderr)

    def test_helper_outputs_cannot_alias_or_modify_the_worktree(self):
        pin = self.write_data("b" * 64, "2026-09-18T12:34:56Z")
        refresh, verification = self.results(pin)
        latest = self.root / "data/latest.json"
        original = latest.read_bytes()

        result = self.run_helper(refresh, verification, code=1, output_path=latest)[0]
        self.assertIn("outside the repository worktree", result.stderr)
        self.assertEqual(latest.read_bytes(), original)

        symlink = self.metadata / "output-symlink"
        symlink.symlink_to(latest)
        result = self.run_helper(refresh, verification, code=1, output_path=symlink)[0]
        self.assertIn("outside the repository worktree", result.stderr)
        self.assertEqual(latest.read_bytes(), original)

        hardlink = self.metadata / "output-hardlink"
        os.link(latest, hardlink)
        result = self.run_helper(refresh, verification, code=1, output_path=hardlink)[0]
        self.assertIn("regular file with exactly one link", result.stderr)
        self.assertEqual(latest.read_bytes(), original)
        hardlink.unlink()

        case_alias = Path(str(self.root).upper()) / "DATA/LATEST.JSON"
        result = self.run_helper(refresh, verification, code=1, output_path=case_alias)[0]
        self.assertIn("outside the repository worktree", result.stderr)
        self.assertEqual(latest.read_bytes(), original)

        overlap = self.metadata / "overlap"
        result = self.run_helper(
            refresh, verification, code=1, output_path=overlap, summary_path=overlap)[0]
        self.assertIn("overlaps another helper path", result.stderr)

        portable_output = self.metadata / "metadata-é"
        portable_summary = self.metadata / "METADATA-E\u0301"
        result = self.run_helper(
            refresh, verification, code=1,
            output_path=portable_output, summary_path=portable_summary)[0]
        self.assertIn("overlaps another helper path", result.stderr)

    def test_status_and_reported_inventory_must_match_git(self):
        pin = self.write_data("b" * 64, "2026-09-18T12:34:56Z")
        refresh, verification = self.results(pin)
        refresh["status"] = "unchanged"
        self.assertIn("status disagrees", self.run_helper(refresh, verification, code=1)[0].stderr)
        refresh["status"] = "updated"
        refresh["changes"]["added"] = []
        refresh["changes"]["unchanged"] = [pin["url"]]
        self.assertIn("added inventory disagrees", self.run_helper(refresh, verification, code=1)[0].stderr)

    def test_workflow_commits_data_and_dispatches_pages_with_failure_gates(self):
        workflow = WORKFLOW.read_text(encoding="utf-8")
        for required in (
            "cron: '17 18 * * 1'", "workflow_dispatch:", "actions: write", "contents: write", "pages: read",
            "python3 scripts/update_browser.py", "python3 scripts/prepare_scheduled_refresh.py",
            "python3 -m orrery_data verify-browser --directory data", "git add -A -- data",
            "git push origin HEAD:refs/heads/master",
            "gh workflow run browser-pages.yml --ref master -f force=false",
            "python -m unittest discover -s tests -v", "npm ci", "npm test", "FULL_BROWSER_DATA: data",
            "python-version: '3.11'", "python-version: '3.12'", "python-version: '3.13'",
            "python3 scripts/prepare_browser_site.py", "id: publication",
            "if: steps.publication.outputs.status == 'prepared'",
            "actions/cache/restore@v6", "actions/cache/save@v6", "path: .data/http-cache",
            "mpc-http-${{ runner.os }}-lookup-${{ github.run_id }}-${{ github.run_attempt }}",
            "mpc-http-${{ runner.os }}-content-", "hashFiles('.data/http-cache/**')",
            "if: github.ref == 'refs/heads/master'", "ref: master",
            "if: steps.validation.outputs.changed == 'true'",
        ):
            self.assertIn(required, workflow)
        for forbidden in (
                "--allow-count-decrease", "pull-requests: write", "gh pr create",
                "automation/mpc-refresh-", "git push --force"):
            self.assertNotIn(forbidden, workflow)
        self.assertEqual(workflow.count("python -m unittest discover -s tests -v"), 3)
        self.assertEqual(workflow.count("if: steps.validation.outputs.changed == 'true'"), 1)
        self.assertLess(
            workflow.index("python -m unittest discover -s tests -v"),
            workflow.index("git push origin HEAD:refs/heads/master"),
        )
        self.assertLess(
            workflow.index("npm test"),
            workflow.index("git push origin HEAD:refs/heads/master"),
        )
        self.assertLess(
            workflow.index("python3 scripts/prepare_browser_site.py"),
            workflow.index("git push origin HEAD:refs/heads/master"),
        )
        self.assertLess(
            workflow.index("git push origin HEAD:refs/heads/master"),
            workflow.index("gh workflow run browser-pages.yml --ref master -f force=false"),
        )


if __name__ == "__main__":
    unittest.main()
