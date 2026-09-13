#!/usr/bin/env python3
"""The manual workflow's locally testable preparation boundary (no publication)."""

import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from orrery_data.releases import COMMIT, validate_baseline
from orrery_data.paths import validate_writable_path
from orrery_data.storage import atomic_json, loads_json, writer_lock


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work-dir", type=Path, default=ROOT / "artifacts" / "preparation")
    # Local HTTP fixtures exercise the exact same entry point; workflow uses MPC defaults.
    parser.add_argument("--mpcorb-url")
    parser.add_argument("--numbered-url")
    args = parser.parse_args()
    args.work_dir = args.work_dir.resolve()
    try:
        for key in ("RELEASE_PRODUCER_COMMIT", "RELEASE_BASELINE_COUNTS"):
            if not os.environ.get(key, "").strip():
                raise ValueError(f"Environment variable {key} is required")
        commit = os.environ["RELEASE_PRODUCER_COMMIT"]
        if not re.fullmatch(COMMIT, commit):
            raise ValueError("RELEASE_PRODUCER_COMMIT must be a full 40-character lowercase Git SHA")
        baseline = validate_baseline(loads_json(os.environ["RELEASE_BASELINE_COUNTS"]))
        limits = [n.strip() for n in os.environ.get("RELEASE_SELECTED_LIMITS", "100000").split(",") if n.strip()]
        if not limits or not all(n.isascii() and n.isdecimal() and int(n) > 0 for n in limits):
            raise ValueError("Selected limits must be comma-separated positive integers")
        allow = os.environ.get("RELEASE_ALLOW_COUNT_DECREASE", "false")
        if allow not in ("true", "false"):
            raise ValueError("Count decrease input must be true or false")
        validate_writable_path(args.work_dir, label="Workflow work directory")
        for name in ("baseline-counts.json", "prepared-release.json"):
            validate_writable_path(args.work_dir / name, label="Workflow metadata")
        for key in ("GITHUB_OUTPUT", "GITHUB_STEP_SUMMARY"):
            if os.environ.get(key):
                validate_writable_path(os.environ[key], label=key)
        with writer_lock(args.work_dir):
            return prepare(args, commit, baseline, limits, allow)
    except subprocess.CalledProcessError as exc:
        print(exc.stderr or str(exc), file=sys.stderr)
        return 1
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(str(exc), file=sys.stderr)
        return 1


def prepare(args, commit, baseline, limits, allow):
    # The helper lock covers both baseline publication and subprocess reads,
    # including verification and result publication for this invocation.
    baseline_file = args.work_dir / "baseline-counts.json"
    atomic_json(baseline_file, baseline)
    command = [sys.executable, "-m", "orrery_data", "prepare-release",
               "--store", str(args.work_dir / "store"), "--output", str(args.work_dir / "releases"),
               "--producer-commit", commit,
               "--baseline-counts", str(baseline_file)]
    for limit in limits:
        command.extend(["--selected-limit", limit])
    if allow == "true":
        command.append("--allow-count-decrease")
    for name in ("mpcorb", "numbered"):
        url = getattr(args, name + "_url")
        if url:
            command.extend(["--" + name + "-url", url])
    prepared = subprocess.run(command, cwd=ROOT, text=True, capture_output=True, check=True)
    result = json.loads(prepared.stdout)
    # A separate process verifies exactly the path later passed to upload-artifact.
    subprocess.run([sys.executable, "-m", "orrery_data", "verify-release", "--bundle", result["path"],
                    "--manifest-sha256", result["manifest"]["sha256"]], cwd=ROOT, check=True)
    atomic_json(args.work_dir / "prepared-release.json", result)
    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a") as stream:
            stream.write(f"bundle={result['path']}\nrelease_version={result['release_version']}\n")
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as stream:
            stream.write("Prepared a release candidate. No GitHub Release was published.\n\n"
                         f"```json\n{json.dumps(result, indent=2)}\n```\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
