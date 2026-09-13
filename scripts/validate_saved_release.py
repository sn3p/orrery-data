#!/usr/bin/env python3
"""Validate a release candidate against the full retained 2026-09-12 dataset."""

import argparse
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
# Retained source identity recorded in docs/release-validation-result.json.
EXPECTED_SNAPSHOT_VERSION = "snapshot-v1-53b641e2f4ae173bb0da258b6d65dd6de8c752b99fa3ce267c96bacc249a69e5"
EXPECTED_COUNTS = {"orbital_records": 1563495, "master_records": 1563495,
                   "known_discovery": 895910, "missing_discovery": 667585,
                   "numbered_orbits": 895910, "unnumbered_orbits": 667585, "unsupported_orbits": 0,
                   "discovery_records": 895910, "unmatched_discovery_records": 0}
PRODUCER_PATHS = ("orrery_data", "scripts", "pyproject.toml")


def arguments():
    if not __debug__:
        raise SystemExit("Saved release validation requires assertions; run Python without "
                         "-O/-OO or PYTHONOPTIMIZE.")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--store", type=Path, required=True)
    parser.add_argument("--reference-exports", type=Path, required=True)
    parser.add_argument("--work-dir", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--clone-copy", action="store_true", help="use macOS APFS copy-on-write for the standalone copy")
    args = parser.parse_args()
    for key in ("store", "reference_exports", "work_dir", "report"):
        setattr(args, key, getattr(args, key).resolve())
    if args.clone_copy and sys.platform != "darwin":
        parser.error("--clone-copy requires macOS APFS")
    return args


def copy_committed_producer(commit, destination):
    # Disable local replacement refs for both tree and blob reads, so these
    # are the named commit's objects rather than substitute code.
    # HEAD and the original worktree can change after this point without
    # affecting any validator helper or producer process in this run.
    entries = subprocess.check_output(["git", "--no-replace-objects", "ls-tree", "-rz", commit, "--", *PRODUCER_PATHS], cwd=ROOT)
    for entry in entries.split(b"\0"):
        if not entry:
            continue
        metadata, name = entry.split(b"\t", 1)
        mode, kind, object_id = metadata.split()
        if kind != b"blob" or mode not in (b"100644", b"100755"):
            raise RuntimeError("Committed producer must contain only regular files")
        target = destination / name.decode("utf-8")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(subprocess.check_output(["git", "--no-replace-objects", "cat-file", "blob", object_id.decode("ascii")], cwd=ROOT))
        target.chmod(0o555 if mode == b"100755" else 0o444)


def producer_command(*args):
    script = ("import runpy, sys\n"
              "sys.path.insert(0, sys.argv.pop(1))\n"
              "runpy.run_module('orrery_data', run_name='__main__')\n")
    return [sys.executable, "-I", "-B", "-c", script, str(ROOT), *map(str, args)]


def cli(*args):
    started = time.monotonic()
    result = subprocess.run(producer_command(*args), cwd=ROOT, capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError(result.stderr)
    return json.loads(result.stdout), round(time.monotonic() - started, 3)


def main():
    args = arguments()  # Reject optimization and invalid arguments before creating files.
    commit = subprocess.check_output(["git", "--no-replace-objects", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    subprocess.run(["git", "--no-replace-objects", "diff", "--exit-code", commit, "--", *PRODUCER_PATHS], cwd=ROOT, check=True)
    with tempfile.TemporaryDirectory(prefix="orrery-release-producer-") as temp:
        producer = Path(temp)
        copy_committed_producer(commit, producer)
        # Run the validator and its independent SQL helper from that same
        # committed snapshot. Isolated mode excludes ambient PYTHONPATH/code.
        script = ("import runpy, sys\n"
                  "root, commit = sys.argv[1:3]\n"
                  "sys.argv = [root + '/scripts/validate_saved_release.py', *sys.argv[3:]]\n"
                  "sys.path.insert(0, root + '/scripts')\n"
                  "validator = runpy.run_path(sys.argv[0])\n"
                  "validator['validate'](validator['arguments'](), commit)\n")
        options = ["--store", str(args.store), "--reference-exports", str(args.reference_exports),
                   "--work-dir", str(args.work_dir), "--report", str(args.report)]
        if args.clone_copy:
            options.append("--clone-copy")
        result = subprocess.run([sys.executable, "-I", "-B", "-c", script, str(producer), commit, *options],
                                cwd=producer)
        if result.returncode:
            raise SystemExit(result.returncode)


def reference_exports(directory):
    from orrery_data.pipeline import validate_export_manifest

    references = {}
    for path in sorted(directory.glob("*/manifest.json")):
        try:
            manifest = json.loads(path.read_text())
            validate_export_manifest(manifest)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise SystemExit(f"Invalid reference export {path}: {exc}") from exc
        limit = manifest["selection"]["limit"]
        if limit in references and references[limit]["artifacts"] != manifest["artifacts"]:
            raise SystemExit(f"Conflicting reference exports for limit {limit} in {directory}")
        references[limit] = manifest
    for limit in (None, 100000):
        if limit not in references:
            raise SystemExit(f"Reference export for limit {limit} not found in {directory}")
    return references


def validate(args, commit):
    sys.path.insert(0, str(ROOT))
    from orrery_data.pipeline import load_snapshot
    from validate_saved_database import MASTER_SHA256, sha, verify_rows

    version = EXPECTED_SNAPSHOT_VERSION
    try:
        snapshot_dir, snapshot = load_snapshot(args.store, version)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise SystemExit(f"Requires retained validated 2026-09-12 source snapshot {version}: {exc}") from exc
    if snapshot["counts"] != EXPECTED_COUNTS:
        raise SystemExit("Requires retained validated snapshot counts: all nine fields must match the 2026-09-12 evidence")
    master = snapshot_dir / "master.jsonl.gz"
    assert sha(master) == MASTER_SHA256, "requires retained validated master"
    references = reference_exports(args.reference_exports)
    args.work_dir.mkdir(parents=True, exist_ok=True)
    timings = {}
    output = Path(tempfile.mkdtemp(prefix="releases-", dir=args.work_dir))
    print("Preparing full SQLite/full/100k release through the CLI...", flush=True)
    prepared, timings["prepare"] = cli("prepare-release", "--store", args.store, "--snapshot", version,
                                      "--output", output, "--producer-commit", commit)
    bundle = Path(prepared["path"])
    manifest = json.loads((bundle / "release.json").read_text())
    assert prepared["counts"] == EXPECTED_COUNTS, "prepared counts differ from retained validated snapshot counts"
    assert manifest["sources"] == json.loads((snapshot_dir / "snapshot.json").read_text())["sources"]
    print("Comparing every SQLite field against all 1,563,495 saved master rows...", flush=True)
    started = time.monotonic()
    verify_rows(bundle / "orrery.sqlite3", master)
    timings["compare_every_row"] = round(time.monotonic() - started, 3)
    exports = {}
    for profile, limit in (("full", None), ("first-100000", 100000)):
        reference = references[limit]
        exported = json.loads((bundle / f"exports/{profile}/manifest.json").read_text())
        assert exported["artifacts"] == reference["artifacts"], "existing export artifacts changed"
        for name, info in reference["artifacts"].items():
            assert sha(bundle / f"exports/{profile}" / name) == info["sha256"]
        exports[profile] = exported["artifacts"]
    print("Repeating preparation in the same output directory...", flush=True)
    again, timings["repeat"] = cli("prepare-release", "--store", args.store, "--snapshot", version,
                                   "--output", output, "--producer-commit", commit)
    assert again == prepared, "immutable candidate changed on rerun"
    copied = args.work_dir / "downloaded-copy"
    # Always regenerate the copy; stale results must not bypass current transfer verification.
    if copied.exists():
        shutil.rmtree(copied)
    if args.clone_copy:
        subprocess.run(["cp", "-cR", str(bundle), str(copied)], check=True)
    else:
        shutil.copytree(bundle, copied)
    print("Verifying and consuming the standalone copy...", flush=True)
    verified, timings["verify_copy"] = cli("verify-release", "--bundle", copied,
                                          "--manifest-sha256", prepared["manifest"]["sha256"])
    assert verified["release_version"] == prepared["release_version"]
    for mode, count in (("all", 1563495), ("known", 895910), ("missing", 667585)):
        page, _ = cli("query", "--database", copied / "orrery.sqlite3", "--discovery", mode, "--limit", 2)
        assert page["matched_records"] == count
        if mode != "all":
            assert all((r["disc"] is None) == (mode == "missing") for r in page["records"])
    rounded, _ = cli("query", "--database", copied / "orrery.sqlite3", "--packed-designation", "K17S44L")
    assert rounded["records"][0]["w"] == 360.0
    assert rounded["records"][0]["w"] != rounded["records"][0]["W"]
    assert sha(copied / "orrery.sqlite3") == manifest["artifacts"]["orrery.sqlite3"]["sha256"]
    # A damaged transferred payload must fail without changing the original candidate.
    with (copied / "exports/first-100000/catalog.json.gz").open("ab") as stream:
        stream.write(b"damaged transfer")
    rejected = subprocess.run(producer_command("verify-release", "--bundle", copied),
                              cwd=ROOT, capture_output=True, text=True)
    assert rejected.returncode == 1 and "Checksum or size mismatch" in rejected.stderr
    assert sha(bundle / "exports/first-100000/catalog.json.gz") == exports["first-100000"]["catalog.json.gz"]["sha256"]
    shutil.rmtree(copied)
    report = {"status": "passed", "producer_commit": commit, "python": platform.python_version(),
              "release": prepared, "artifact_payload_bytes": sum(v["bytes"] for v in manifest["artifacts"].values()),
              "bundle_bytes": sum(p.stat().st_size for p in bundle.rglob("*") if p.is_file()),
              "every_sqlite_field_matches_master": True, "unchanged_export_artifacts": exports,
              "repeat_same_directory_immutable": True, "standalone_copy_verified_and_queried": True,
              "copy_method": "APFS clone" if args.clone_copy else "byte copy",
              "damaged_copy_rejected": True, "seconds": timings,
              "limits": ["No GitHub-hosted manual workflow run or artifact upload/download",
                         "No release publication, hosting or app/browser/GPU integration"]}
    args.report.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(report, indent=2) + "\n"
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", prefix=".report-",
                                         dir=args.report.parent, delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, args.report)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    print(json.dumps(report, indent=2), flush=True)


if __name__ == "__main__":
    main()
