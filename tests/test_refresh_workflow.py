"""Scheduled acquisition review boundaries and workflow policy."""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from scripts import prepare_refresh_pr as refresh_helper


ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "scripts/prepare_refresh_pr.py"
WORKFLOW = ROOT / ".github/workflows/mpc-refresh.yml"


class RefreshWorkflow(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        subprocess.run(["git", "init", "--quiet"], cwd=self.root, check=True)
        subprocess.run(["git", "config", "user.name", "Test"], cwd=self.root, check=True)
        subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=self.root, check=True)
        (self.root / ".gitignore").write_text(".context/\n", encoding="utf-8")
        self.write_data("a" * 64, "2026-09-12T00:00:00Z")
        self.commit()

    def write_data(self, digest, retrieved_at):
        data = self.root / "data"
        data.mkdir(exist_ok=True)
        for old in data.glob("index-*.json"):
            old.unlink()
        name = f"index-{digest}.json"
        index = {
            "snapshot_version": "snapshot-v1-" + digest,
            "catalog_id": "export-v1-" + digest,
            "counts": {"orbital_records": 3, "master_records": 3, "discovery_records": 2,
                       "known_discovery": 2, "missing_discovery": 1},
            "sources": {
                "mpcorb": {"retrieved_at": retrieved_at, "last_modified": "Friday"},
                "numbered": {"retrieved_at": retrieved_at, "last_modified": "Tuesday"},
            },
        }
        (data / name).write_text(json.dumps(index), encoding="utf-8")
        pin = {"url": name, "sha256": digest, "bytes": (data / name).stat().st_size}
        (data / "latest.json").write_text(json.dumps({"index": pin}), encoding="utf-8")
        return pin

    def commit(self):
        subprocess.run(["git", "add", "-A"], cwd=self.root, check=True)
        subprocess.run(["git", "commit", "--quiet", "-m", "data"], cwd=self.root, check=True)

    def results(self, pin, status="updated"):
        summary = {"path": str(self.root / "data"), "pin": pin,
                   "records": 2, "chunks": 1, "files": 2, "bytes": 100}
        refresh = {**summary, "status": status,
                   "changes": {"added": [pin["url"]] if status == "updated" else [],
                               "changed": ["latest.json"] if status == "updated" else [],
                               "unchanged": [] if status == "updated" else [pin["url"], "latest.json"],
                               "deleted": ["index-" + "a" * 64 + ".json"] if status == "updated" else []}}
        return refresh, summary

    def run_helper(self, refresh, verification, code=0, env_updates=None):
        context = self.root / ".context"
        context.mkdir(exist_ok=True)
        refresh_path, verification_path = context / "refresh.json", context / "verification.json"
        body, output, summary = context / "body.md", context / "output", context / "summary"
        refresh_path.write_text(json.dumps(refresh), encoding="utf-8")
        verification_path.write_text(json.dumps(verification), encoding="utf-8")
        env = {**os.environ, "GITHUB_RUN_ID": "12345", "GITHUB_RUN_ATTEMPT": "2",
               "GITHUB_REPOSITORY": "sn3p/orrery-data", "GITHUB_SERVER_URL": "https://github.test"}
        env.update(env_updates or {})
        result = subprocess.run([
            sys.executable, HELPER, "--repository", self.root,
            "--refresh-result", refresh_path, "--verification-result", verification_path,
            "--body", body, "--github-output", output, "--github-summary", summary,
        ], capture_output=True, text=True, env=env)
        self.assertEqual(result.returncode, code, result.stdout + result.stderr)
        return result, body, output, summary

    def test_changed_data_prepares_unique_draft_pr_metadata(self):
        pin = self.write_data("b" * 64, "2026-09-18T12:34:56Z")
        refresh, verification = self.results(pin)
        result, body, output, summary = self.run_helper(refresh, verification)
        value = json.loads(result.stdout)
        self.assertTrue(value["changed"])
        self.assertEqual(value["branch"], "automation/mpc-refresh-12345-2")
        self.assertIn("Refresh MPC browser data (2026-09-18)", output.read_text())
        self.assertIn("never auto-merged", body.read_text())
        self.assertIn("https://github.test/sn3p/orrery-data/actions/runs/12345", body.read_text())
        self.assertIn("review required", summary.read_text())

    def test_no_data_diff_reports_noop(self):
        pin = json.loads((self.root / "data/latest.json").read_text())["index"]
        refresh, verification = self.results(pin, status="unchanged")
        result, _, output, summary = self.run_helper(refresh, verification)
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
        index.write_text(json.dumps(value), encoding="utf-8")
        pin["bytes"] = index.stat().st_size
        latest = json.loads((self.root / "data/latest.json").read_text())
        latest["index"] = pin
        (self.root / "data/latest.json").write_text(json.dumps(latest), encoding="utf-8")
        refresh, verification = self.results(pin)
        self.assertIn("decreased from committed 3 to 2", self.run_helper(refresh, verification, code=1)[0].stderr)

    def test_pr_metadata_count_is_validated(self):
        pin = self.write_data("b" * 64, "2026-09-18T12:34:56Z")
        index = self.root / "data" / pin["url"]
        value = json.loads(index.read_text())
        del value["counts"]["missing_discovery"]
        index.write_text(json.dumps(value), encoding="utf-8")
        pin["bytes"] = index.stat().st_size
        latest = json.loads((self.root / "data/latest.json").read_text())
        latest["index"] = pin
        (self.root / "data/latest.json").write_text(json.dumps(latest), encoding="utf-8")
        refresh, verification = self.results(pin)
        result = self.run_helper(refresh, verification, code=1)[0]
        self.assertIn("missing required count missing_discovery", result.stderr)

    def test_run_identity_requires_positive_ascii_integers(self):
        pin = self.write_data("b" * 64, "2026-09-18T12:34:56Z")
        refresh, verification = self.results(pin)
        for environment in (
                {"GITHUB_RUN_ID": "0"}, {"GITHUB_RUN_ATTEMPT": "0"},
                {"GITHUB_RUN_ID": "１２３"}):
            with self.subTest(environment=environment):
                result = self.run_helper(
                    refresh, verification, code=1, env_updates=environment)[0]
                self.assertIn("must be positive integers", result.stderr)

    def test_status_and_reported_inventory_must_match_git(self):
        pin = self.write_data("b" * 64, "2026-09-18T12:34:56Z")
        refresh, verification = self.results(pin)
        refresh["status"] = "unchanged"
        self.assertIn("status disagrees", self.run_helper(refresh, verification, code=1)[0].stderr)
        refresh["status"] = "updated"
        refresh["changes"]["added"] = []
        refresh["changes"]["unchanged"] = [pin["url"]]
        self.assertIn("added inventory disagrees", self.run_helper(refresh, verification, code=1)[0].stderr)

    def test_workflow_keeps_review_and_failure_gates(self):
        workflow = WORKFLOW.read_text(encoding="utf-8")
        for required in (
            "cron: '17 18 * * 1'", "workflow_dispatch:", "contents: write", "pull-requests: write",
            "automation/mpc-refresh-", "python3 scripts/update_browser.py",
            "python3 -m orrery_data verify-browser --directory data", "gh pr create --draft --base master",
            "actions/cache/restore@v6", "actions/cache/save@v6", "path: .data/http-cache",
            "mpc-http-${{ runner.os }}-lookup-${{ github.run_id }}-${{ github.run_attempt }}",
            "mpc-http-${{ runner.os }}-content-", "hashFiles('.data/http-cache/**')",
            "if: github.ref == 'refs/heads/master'", "ref: master", ".isCrossRepository == false",
            "git push origin --delete \"$REFRESH_BRANCH\"",
        ):
            self.assertIn(required, workflow)
        self.assertNotIn("--allow-count-decrease", workflow)
        self.assertNotIn("git push origin master", workflow)
        self.assertNotIn(
            "key: mpc-http-${{ runner.os }}-${{ github.run_id }}-${{ github.run_attempt }}", workflow)


if __name__ == "__main__":
    unittest.main()
