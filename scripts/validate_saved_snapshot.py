#!/usr/bin/env python3
"""Opt-in full-source CLI regression; inputs are local and never downloaded.

Run from the repository root. See docs/validation.md for usage and provenance.
"""

import argparse
from datetime import datetime
import gzip
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time


ROOT = Path(__file__).resolve().parents[1]
FIELDS = ("disc", "epoch", "a", "e", "i", "W", "w", "M", "n")
EXPECTED = {"orbital_records": 1563495, "master_records": 1563495,
            "known_discovery": 895910, "missing_discovery": 667585,
            "unsupported_orbits": 0, "unmatched_discovery_records": 0}
HASHES = {"mpcorb": "14ee5188cb0188b5e2cefba10cbed0312b3fef7a3020a0e482541370ca48b3e2",
          "numbered": "a3cea29a490e2efb4daa34782746caf301634575f2314d1eb3f537e233603a90"}


def cli(*args):
    started = time.monotonic()
    result = subprocess.run([sys.executable, "-m", "orrery_data", *map(str, args)],
                            cwd=ROOT, capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError(f"CLI exited {result.returncode}: {result.stderr}")
    return json.loads(result.stdout), round(time.monotonic() - started, 3)


def jd(day):
    # Independent oracle: the original Orrery importer calendar conversion.
    return (day - datetime(2000, 1, 1)).days + 2451544.5


def legacy_rows(sources):
    dates = {}
    with gzip.open(sources / "NumberedMPs.txt.gz", "rt") as stream:
        for line in stream:
            number = int(line[1:7].strip().replace("(", ""))
            dates[number] = jd(datetime.strptime(line[41:51], "%Y %m %d"))
    expected = []
    with gzip.open(sources / "MPCORB.DAT.gz", "rt") as stream:
        for line in stream:
            number = line[167:173].strip().replace("(", "")
            if not number.isdigit() or int(number) not in dates:
                continue
            _, _, _, epoch, M, w, W, i, e, n, a, _ = line.split(None, 11)
            day = datetime(int(str(int(epoch[0], 36)) + epoch[1:3]), int(epoch[3], 36), int(epoch[4], 36))
            expected.append((dates[int(number)], jd(day), float(a), float(e), float(i), float(W), float(w), float(M), float(n)))
    return expected


def verify_checksums(directory):
    for line in (directory / "SHA256SUMS").read_text().splitlines():
        expected, name = line.split("  ")
        with (directory / name).open("rb") as stream:
            assert hashlib.file_digest(stream, "sha256").hexdigest() == expected, name


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sources", type=Path, required=True)
    parser.add_argument("--work-dir", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    args.work_dir.mkdir(parents=True, exist_ok=True)
    metadata = {}
    for name, filename in (("mpcorb", "MPCORB"), ("numbered", "NumberedMPs")):
        headers = {}
        for line in (args.sources / f"{filename}.headers").read_text().splitlines():
            if ": " in line:
                key, value = line.split(": ", 1)
                headers[key.lower()] = value
        metadata[name] = {"sha256": HASHES[name], "etag": headers["etag"],
                          "last_modified": headers["last-modified"], "content_length": headers["content-length"],
                          "retrieved_at": "2026-09-12T14:57:32Z"}
    metadata["numbered"]["decoded_sha256"] = "56229cf0f045beb62180a62f4b9a9351af9955e7e88aab5976256fa62347a705"
    metadata_path = args.work_dir / "source-metadata.json"
    metadata_path.write_text(json.dumps(metadata, indent=2) + "\n")
    store = args.work_dir / "store"
    output = args.work_dir / "releases"
    inputs = ["--mpcorb", args.sources / "MPCORB.DAT.gz", "--numbered", args.sources / "NumberedMPs.txt.gz",
              "--source-metadata", metadata_path]
    print("Validating saved sources through refresh...", flush=True)
    snapshot, refresh_seconds = cli("refresh", "--store", store, *inputs)
    for key, value in EXPECTED.items():
        assert snapshot["counts"][key] == value, (key, snapshot["counts"][key])
    print("Exporting all eligible discovery rows...", flush=True)
    full, export_seconds = cli("export", "--store", store, "--output", output)
    limited, limited_seconds = cli("export", "--store", store, "--output", output, "--limit", "100000")
    print("Comparing all nine numeric fields to the original importer's algorithm...", flush=True)
    expected = legacy_rows(args.sources)
    for result, rows in ((full, expected), (limited, expected[:100000])):
        path = Path(result["path"])
        rows = sorted(rows, key=lambda r: r[0])
        with (path / "catalog.json").open() as stream:
            catalog = json.load(stream)
        assert len(catalog) == len(rows)
        for record, legacy in zip(catalog, rows):
            assert set(record) == set(FIELDS)
            assert tuple(record[k] for k in FIELDS) == legacy
        del catalog, rows
        verify_checksums(path)
    del expected
    print("Checking master identities, nulls and complete record coverage...", flush=True)
    ids, nulls, known = set(), 0, 0
    with gzip.open(Path(full["path"]) / "master.jsonl.gz", "rt") as stream:
        for line in stream:
            row = json.loads(line)
            assert row["id"] not in ids
            ids.add(row["id"])
            if row["disc"] is None:
                nulls += 1
            else:
                known += 1
    assert (len(ids), known, nulls) == (1563495, 895910, 667585)
    del ids
    print("Regenerating from scratch to verify deterministic artifact bytes...", flush=True)
    with tempfile.TemporaryDirectory(prefix="rebuild-", dir=args.work_dir) as temp:
        temp = Path(temp)
        rebuilt, _ = cli("refresh", "--store", temp / "store", *inputs)
        again, _ = cli("export", "--store", temp / "store", "--output", temp / "releases")
        assert rebuilt["snapshot_version"] == snapshot["snapshot_version"]
        assert again["data_version"] == full["data_version"]
        assert again["artifacts"] == full["artifacts"]
    result = {"status": "passed", "source_snapshot": "2026-09-12", "snapshot_version": snapshot["snapshot_version"],
              "counts": snapshot["counts"], "full_export": full, "limited_export": limited,
              "checks": ["source hashes", "complete counts", "unique identities", "null discovery coverage",
                         "all nine fields equal original importer (all and 100k)", "manifest checksums", "fresh-store determinism"],
              "timings_seconds": {"refresh": refresh_seconds, "export_all": export_seconds, "export_100k": limited_seconds},
              "limits": "No browser/GPU rendering, transfer, preparation or playback performance validation."}
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({"status": "passed", "report": str(args.report), "counts": snapshot["counts"]}), flush=True)


if __name__ == "__main__":
    main()
