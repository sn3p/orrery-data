#!/usr/bin/env python3
"""Opt-in full SQLite/JSON regression against the retained validated 2026-09-12 snapshot."""

import argparse
from contextlib import closing
import gzip
import hashlib
import itertools
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import time


ROOT = Path(__file__).resolve().parents[1]
FIELDS = ("id", "number", "packed_designation", "readable_designation", "disc", "epoch",
          "a", "e", "i", "W", "w", "M", "n", "orbit_reference", "orbit_computer")
# Independent SQL projection: avoid importing the builder's mapping as the oracle.
SQL = """SELECT o.id, o.number, o.packed_designation, o.readable_designation, o.disc,
 r.epoch, r.a, r.e, r.i, r.ascending_node, r.perihelion_argument, r.M, r.n,
 r.orbit_reference, r.orbit_computer FROM objects o JOIN orbits r USING (source_order)
 ORDER BY o.source_order"""
EXPECTED = {"master_records": 1563495, "known_discovery": 895910, "missing_discovery": 667585}
MASTER_SHA256 = "822b409ff3bf5d4086f21d027fbcff58e7632f320806071ea97b7d9eba10a2ee"


def sha(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def cli(*args):
    started = time.monotonic()
    result = subprocess.run([sys.executable, "-m", "orrery_data", *map(str, args)],
                            cwd=ROOT, capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError(result.stderr)
    return json.loads(result.stdout), round(time.monotonic() - started, 3)


def verify_rows(database, master):
    counts = {key: 0 for key in EXPECTED}
    with closing(sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True)) as con:
        assert con.execute("PRAGMA integrity_check").fetchall() == [("ok",)]
        assert con.execute("PRAGMA foreign_key_check").fetchall() == []
        with gzip.open(master, "rt") as stream:
            for actual, line in itertools.zip_longest(con.execute(SQL), stream):
                assert actual is not None and line is not None, "row count mismatch"
                expected = json.loads(line)
                assert dict(zip(FIELDS, actual)) == expected, expected["id"]
                counts["master_records"] += 1
                counts["missing_discovery" if expected["disc"] is None else "known_discovery"] += 1
    assert counts == EXPECTED, counts


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--store", type=Path, required=True)
    parser.add_argument("--reference-exports", type=Path, required=True)
    parser.add_argument("--work-dir", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    args.work_dir.mkdir(parents=True, exist_ok=True)
    database = args.work_dir / "orrery.sqlite3"
    pointer = json.loads((args.store / "current.json").read_text())
    version = pointer["snapshot_version"]
    snapshot = args.store / "snapshots" / version
    master = snapshot / "master.jsonl.gz"
    assert sha(master) == MASTER_SHA256, "requires the retained validated 2026-09-12 master"
    timings = {}
    print("Building complete SQLite database through the CLI...", flush=True)
    built, timings["build"] = cli("build-db", "--store", args.store, "--snapshot", version, "--database", database)
    assert built["counts"] == EXPECTED
    assert sha(database) == built["artifact"]["sha256"]
    print("Comparing every stored field to all 1,563,495 master rows...", flush=True)
    verify_rows(database, master)
    info, timings["info_verify"] = cli("db-info", "--database", database, "--verify")
    assert info["counts"] == EXPECTED and info["integrity"] == "ok"
    assert info["snapshot"] == json.loads((snapshot / "snapshot.json").read_text())
    assert info["mpcorb_header"] == (snapshot / "MPCORB-header.txt").read_text()
    assert info["notice"] == (ROOT / "orrery_data/NOTICE.txt").read_text()
    print("Checking real CLI lookup, dated/null filters, date bounds and pagination...", flush=True)
    for mode, count in (("all", 1563495), ("known", 895910), ("missing", 667585)):
        page, timings[f"query_{mode}"] = cli("query", "--database", database, "--discovery", mode, "--limit", 2)
        assert page["matched_records"] == count and len(page["records"]) == 2
        if mode != "all":
            assert all((r["disc"] is None) == (mode == "missing") for r in page["records"])
    ceres, _ = cli("query", "--database", database, "--number", 1)
    assert ceres["records"][0]["id"] == "mpc:number:1"
    dated, _ = cli("query", "--database", database, "--discovered-from", "1801-01-01", "--discovered-to", "1801-01-01")
    assert ceres["records"][0] in dated["records"]
    rounded, _ = cli("query", "--database", database, "--packed-designation", "K17S44L")
    assert rounded["records"][0]["w"] == 360.0
    last, _ = cli("query", "--database", database, "--offset", 1563494)
    assert len(last["records"]) == 1
    assert sha(database) == built["artifact"]["sha256"], "read commands mutated the database"
    print("Repeating the full build and every-row comparison...", flush=True)
    again, timings["repeat_build"] = cli("build-db", "--store", args.store, "--snapshot", version, "--database", database)
    assert again["database_version"] == built["database_version"]
    verify_rows(database, master)
    print("Re-exporting all/100k JSON and comparing exact retained artifact hashes...", flush=True)
    exports = []
    for limit in (None, 100000):
        flags = [] if limit is None else ["--limit", limit]
        exported, timings[f"export_{limit or 'all'}"] = cli("export", "--store", args.store, "--snapshot", version,
                                                           "--output", args.work_dir / "exports", *flags)
        references = [json.loads(p.read_text()) for p in args.reference_exports.glob("*/manifest.json")]
        reference = next(r for r in references if r["selection"]["limit"] == limit)
        assert exported["artifacts"] == reference["artifacts"]
        directory = Path(exported["path"])
        for line in (directory / "SHA256SUMS").read_text().splitlines():
            expected, name = line.split("  ")
            assert sha(directory / name) == expected
        exports.append({"limit": limit, "artifacts": exported["artifacts"]})
    report = {"status": "passed", "source_snapshot": "2026-09-12", "snapshot_version": version,
              "database_version": again["database_version"], "database_schema_version": again["database_schema_version"],
              "tool_version": info["tool_version"], "sqlite_version": sqlite3.sqlite_version,
              "counts": EXPECTED, "database_artifact": again["artifact"], "master_sha256": MASTER_SHA256,
              "exports": exports, "timings_seconds": timings,
              "checks": ["existing snapshot checksums", "every master field equals SQLite on first and repeated build",
                         "complete counts and unique identities", "integrity and foreign keys", "provenance/header/notice equality",
                         "real CLI identity/date/null/pagination queries", "read commands retain database bytes",
                         "full and 100k JSON exports equal retained artifact hashes"],
              "limits": "Local producer only; no HTTP hosting, release publication, browser or app integration verification."}
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({"status": "passed", "report": str(args.report), "counts": EXPECTED}), flush=True)


if __name__ == "__main__":
    main()
