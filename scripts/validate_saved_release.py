#!/usr/bin/env python3
"""Validate a release candidate against the full retained 2026-09-12 dataset."""

import argparse
import json
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tempfile
import time

from validate_saved_database import EXPECTED, MASTER_SHA256, ROOT, cli, sha, verify_rows


def main():
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
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    subprocess.run(["git", "diff", "--exit-code", "HEAD", "--", "orrery_data", "scripts", "pyproject.toml"],
                   cwd=ROOT, check=True)
    version = json.loads((args.store / "current.json").read_text())["snapshot_version"]
    snapshot_dir = args.store / "snapshots" / version
    master = snapshot_dir / "master.jsonl.gz"
    assert sha(master) == MASTER_SHA256, "requires retained validated master"
    args.work_dir.mkdir(parents=True, exist_ok=True)
    timings = {}
    output = Path(tempfile.mkdtemp(prefix="releases-", dir=args.work_dir))
    print("Preparing full SQLite/full/100k release through the CLI...", flush=True)
    prepared, timings["prepare"] = cli("prepare-release", "--store", args.store, "--snapshot", version,
                                      "--output", output, "--producer-commit", commit)
    bundle = Path(prepared["path"])
    manifest = json.loads((bundle / "release.json").read_text())
    assert all(prepared["counts"][k] == v for k, v in EXPECTED.items())
    assert manifest["sources"] == json.loads((snapshot_dir / "snapshot.json").read_text())["sources"]
    print("Comparing every SQLite field against all 1,563,495 saved master rows...", flush=True)
    started = time.monotonic()
    verify_rows(bundle / "orrery.sqlite3", master)
    timings["compare_every_row"] = round(time.monotonic() - started, 3)
    references = [json.loads(p.read_text()) for p in args.reference_exports.glob("*/manifest.json")]
    exports = {}
    for profile, limit in (("full", None), ("first-100000", 100000)):
        reference = next(r for r in references if r["selection"]["limit"] == limit)
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
    rejected = subprocess.run([sys.executable, "-m", "orrery_data", "verify-release", "--bundle", str(copied)],
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
    args.report.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2), flush=True)


if __name__ == "__main__":
    main()
