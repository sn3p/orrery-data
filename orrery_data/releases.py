"""Prepare immutable, portable release candidates; never publish them."""

import gzip
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import re
import shutil
import sqlite3
import tempfile
import zlib

from . import SCHEMA_VERSION, __version__
from .database import DATABASE_SCHEMA_VERSION, build_database, database_info
from .formats import DataError, FIELDS
from .pipeline import export, load_snapshot, refresh, validate_snapshot_manifest
from .storage import (atomic_json, digest, file_info, now, read_json, verify_file,
                      write_json, writer_lock)

RELEASE_SCHEMA_VERSION = 1
COUNT_KEYS = ("orbital_records", "discovery_records", "master_records", "known_discovery")
EXPORT_FILES = {"master.jsonl.gz", "catalog.json", "catalog.json.gz", "MPCORB-header.txt", "NOTICE.txt"}
SHA256 = r"[a-f0-9]{64}"
COMMIT = r"[a-f0-9]{40}"


def require(condition, message):
    if not condition:
        raise DataError(message)


def validate_baseline(counts):
    require(isinstance(counts, dict) and set(counts) == set(COUNT_KEYS)
            and all(type(v) is int and v >= 0 for v in counts.values()),
            "Baseline counts require four nonnegative integers: " + ", ".join(COUNT_KEYS))
    return counts


def dataset_identity(snapshot):
    # This identifies upstream content, independently of parsers, packaging or code releases.
    return {"sources": {name: snapshot["sources"][name]["decoded"]["sha256"]
                        for name in ("mpcorb", "numbered")}}


def selections(limits):
    require(isinstance(limits, list) and bool(limits)
            and all(type(n) is int and n > 0 for n in limits)
            and limits == sorted(set(limits)), "Selected limits must be unique sorted positive integers")
    return {"full": None, **{f"first-{n}": n for n in limits}}


def selection(limit):
    return {"profile": "discovery", "limit": limit,
            "select": "first-known-dates-in-mpcorb-order", "sort": "disc-ascending-stable"}


def inventory(directory):
    require(directory.is_dir() and not directory.is_symlink(), "Bundle must be a real directory")
    result = set()
    for path in directory.rglob("*"):
        require(not path.is_symlink(), "Bundle must not contain symlinks")
        if path.is_dir():
            continue
        require(path.is_file(), "Bundle must contain only regular files")
        result.add(path.relative_to(directory).as_posix())
    return result


def checksums(directory, names):
    return "".join(f"{file_info(directory / name)['sha256']}  {name}\n" for name in sorted(names))


def verify_catalog(directory, manifest, expected_records):
    plain = directory / "catalog.json"
    with gzip.open(directory / "catalog.json.gz", "rb") as stream:
        require(hashlib.file_digest(stream, "sha256").hexdigest() == file_info(plain)["sha256"],
                "Compressed catalog differs from JSON")
    rows = read_json(plain)
    require(isinstance(rows, list) and len(rows) == expected_records, "Catalog count mismatch")
    previous = -math.inf
    for row in rows:
        require(isinstance(row, dict) and set(row) == set(FIELDS)
                and all(type(v) in (int, float) and math.isfinite(v) for v in row.values()),
                "Invalid discovery catalog fields")
        require(row["disc"] >= previous, "Catalog discovery order mismatch")
        previous = row["disc"]
    for name in ("catalog.json", "catalog.json.gz"):
        require(manifest["artifacts"][name]["records"] == expected_records
                and manifest["artifacts"][name]["profile"] == "discovery", "Catalog metadata mismatch")


def verify_release(directory, manifest_sha256=None):
    """Verify a standalone copy without a producer checkout, source store or network."""
    directory = Path(directory)
    actual_files = inventory(directory)
    if manifest_sha256 is not None:
        require(isinstance(manifest_sha256, str) and re.fullmatch(SHA256, manifest_sha256),
                "Expected manifest SHA-256 must be 64 lowercase hexadecimal characters")
        require(file_info(directory / "release.json")["sha256"] == manifest_sha256,
                "Release manifest checksum mismatch")
    manifest = read_json(directory / "release.json")
    identity = manifest["identity"]
    require(manifest["release_schema_version"] == RELEASE_SCHEMA_VERSION
            and identity["release_schema_version"] == RELEASE_SCHEMA_VERSION,
            "Unsupported release schema")
    require(manifest["release_version"] == "release-v1-" + digest(identity), "Release identity mismatch")
    producer = identity["producer"]
    require(isinstance(producer["commit"], str) and re.fullmatch(COMMIT, producer["commit"])
            and isinstance(producer["tool_version"], str) and bool(producer["tool_version"]),
            "Invalid producer identity")
    require(identity["json_schema_version"] == SCHEMA_VERSION
            and identity["database_schema_version"] == DATABASE_SCHEMA_VERSION, "Unsupported artifact schema")
    profiles = selections(identity["selected_limits"])
    expected = {"snapshot.json", "NOTICE.txt", "MPCORB-header.txt", "orrery.sqlite3"}
    for profile in profiles:
        expected.update(f"exports/{profile}/{name}" for name in EXPORT_FILES | {"manifest.json", "SHA256SUMS"})
    # Never follow untrusted manifest paths; the supported layout determines every filename.
    require(set(manifest["artifacts"]) == expected, "Release artifact inventory mismatch")
    require(actual_files == expected | {"release.json", "SHA256SUMS"}, "Bundle file inventory mismatch")
    for name in sorted(expected):
        verify_file(directory / name, manifest["artifacts"][name])
    require((directory / "SHA256SUMS").read_text() == checksums(directory, expected | {"release.json"}),
            "Release SHA256SUMS mismatch")
    snapshot = read_json(directory / "snapshot.json")
    validate_snapshot_manifest(snapshot, identity["snapshot_version"])
    data_identity = dataset_identity(snapshot)
    require(manifest["dataset_identity"] == data_identity
            and identity["dataset_version"] == "data-v1-" + digest(data_identity), "Dataset identity mismatch")
    require(manifest["counts"] == snapshot["counts"] and manifest["sources"] == snapshot["sources"],
            "Release source/count mismatch")
    verify_file(directory / "MPCORB-header.txt", snapshot["files"]["MPCORB-header.txt"])
    require(file_info(directory / "NOTICE.txt")["sha256"] == identity["notice_sha256"], "Attribution mismatch")
    database = database_info(directory / "orrery.sqlite3", verify=True)
    require(database["snapshot"] == snapshot and database["tool_version"] == producer["tool_version"]
            and database["database_version"] == manifest["database_version"]
            and database["mpcorb_header"] == (directory / "MPCORB-header.txt").read_text(encoding="utf-8")
            and database["notice"] == (directory / "NOTICE.txt").read_text(encoding="utf-8"),
            "Database release provenance/version mismatch")
    require(set(manifest["profiles"]) == set(profiles), "Release profile mismatch")
    for profile, limit in profiles.items():
        root = directory / "exports" / profile
        exported = read_json(root / "manifest.json")
        expected_identity = {"snapshot_version": snapshot["snapshot_version"],
                             "tool_version": producer["tool_version"], "schema_version": SCHEMA_VERSION,
                             "selection": selection(limit)}
        count = min(limit, snapshot["counts"]["known_discovery"]) if limit else snapshot["counts"]["known_discovery"]
        require(exported["identity"] == expected_identity
                and exported["data_version"] == "export-v1-" + digest(expected_identity)
                and exported["snapshot_version"] == snapshot["snapshot_version"]
                and exported["tool_version"] == producer["tool_version"]
                and exported["schema_version"] == SCHEMA_VERSION
                and exported["selection"] == selection(limit)
                and exported["sources"] == snapshot["sources"]
                and exported["counts"] == {**snapshot["counts"], "discovery_export": count}
                and exported["exclusions"] == {**snapshot["exclusions"], "selection_limit": snapshot["counts"]["known_discovery"] - count},
                "Export release provenance/version/count mismatch")
        require(manifest["profiles"][profile] == {"data_version": exported["data_version"], "records": count,
                                                "selection": selection(limit)}, "Profile metadata mismatch")
        require(set(exported["artifacts"]) == EXPORT_FILES, "Export artifact inventory mismatch")
        for name in EXPORT_FILES:
            verify_file(root / name, exported["artifacts"][name])
        for name in ("master.jsonl.gz", "MPCORB-header.txt"):
            verify_file(root / name, snapshot["files"][name])
        require(file_info(root / "NOTICE.txt") == file_info(directory / "NOTICE.txt"), "Export attribution mismatch")
        require(exported["artifacts"]["master.jsonl.gz"]["records"] == snapshot["counts"]["master_records"]
                and exported["artifacts"]["master.jsonl.gz"]["profile"] == "master", "Master metadata mismatch")
        require((root / "SHA256SUMS").read_text() == checksums(root, EXPORT_FILES | {"manifest.json"}),
                "Export SHA256SUMS mismatch")
        verify_catalog(root, exported, count)
    return {"status": "verified", "release_version": manifest["release_version"],
            "dataset_version": identity["dataset_version"], "snapshot_version": identity["snapshot_version"],
            "producer": producer, "counts": manifest["counts"], "profiles": manifest["profiles"],
            "manifest": file_info(directory / "release.json"), "path": str(directory)}


def prepare_release(store, output, producer_commit, *, version=None, limits=None, urls=None,
                    local=None, metadata=None, timeout=60, allow_count_decrease=False, baseline_counts=None):
    require(isinstance(producer_commit, str) and re.fullmatch(COMMIT, producer_commit),
            "Producer commit must be a full 40-character lowercase Git SHA")
    limits = sorted(set(limits if limits is not None else [100000]))
    profiles = selections(limits)
    if baseline_counts is not None:
        validate_baseline(baseline_counts)
    output = Path(output).absolute()
    require(not output.is_symlink() and not output.resolve().is_relative_to((store / "snapshots").resolve()),
            "Release output must not be a symlink or be inside immutable snapshots")
    require(version is not None or output.resolve() != store.resolve(),
            "Source store and release output must be different directories when refreshing")
    with writer_lock(output):
        previous_counts = None
        pointer = output / "latest.json"
        if pointer.exists():
            previous_pointer = read_json(pointer)
            previous = previous_pointer["release_version"]
            require(isinstance(previous, str) and re.fullmatch(r"release-v1-" + SHA256, previous),
                    "Invalid latest release pointer")
            previous_counts = verify_release(output / previous, previous_pointer["manifest"]["sha256"])["counts"]
        if version is None:
            version = refresh(store, urls, local or {}, metadata or {}, timeout, allow_count_decrease)["snapshot_version"]
        source, snapshot = load_snapshot(store, version)
        if not allow_count_decrease:
            for baseline in (previous_counts, baseline_counts):
                if baseline is not None:
                    for key in COUNT_KEYS:
                        require(snapshot["counts"][key] >= baseline[key],
                                f"{key} decreased from baseline {baseline[key]} to {snapshot['counts'][key]}; "
                                "inspect sources, then explicitly use --allow-count-decrease if intentional")
        data_identity = dataset_identity(snapshot)
        identity = {"release_schema_version": RELEASE_SCHEMA_VERSION,
                    "dataset_version": "data-v1-" + digest(data_identity), "snapshot_version": version,
                    "producer": {"commit": producer_commit, "tool_version": __version__},
                    "json_schema_version": SCHEMA_VERSION, "database_schema_version": DATABASE_SCHEMA_VERSION,
                    "selected_limits": limits, "notice_sha256": file_info(Path(__file__).with_name("NOTICE.txt"))["sha256"]}
        release_version = "release-v1-" + digest(identity)
        destination = output / release_version
        if destination.exists():
            result = verify_release(destination)
            require(read_json(destination / "release.json")["identity"] == identity, "Existing release identity mismatch")
        else:
            with tempfile.TemporaryDirectory(prefix=".release-", dir=output) as temp:
                stage = Path(temp)
                write_json(stage / "snapshot.json", snapshot)
                shutil.copyfile(source / "MPCORB-header.txt", stage / "MPCORB-header.txt")
                shutil.copyfile(Path(__file__).with_name("NOTICE.txt"), stage / "NOTICE.txt")
                built = build_database(store, stage / "orrery.sqlite3", version)
                (stage / ".lock").unlink()  # Only this private stage owns the builder's lock.
                profile_metadata = {}
                (stage / "exports").mkdir()
                # Each new candidate serializes fresh exports; no previous export cache can mask failures.
                with tempfile.TemporaryDirectory(prefix=".exports-", dir=stage) as export_temp:
                    for profile, limit in profiles.items():
                        exported = export(store, Path(export_temp), version, limit)
                        Path(exported["path"]).rename(stage / "exports" / profile)
                        profile_metadata[profile] = {"data_version": exported["data_version"],
                                                     "records": exported["counts"]["discovery_export"],
                                                     "selection": selection(limit)}
                manifest = {"release_version": release_version, "release_schema_version": RELEASE_SCHEMA_VERSION,
                            "identity": identity, "dataset_identity": data_identity, "created_at": now(),
                            "runtime": {"python": platform.python_version(), "sqlite": sqlite3.sqlite_version,
                                        "zlib": zlib.ZLIB_VERSION},
                            "sources": snapshot["sources"], "counts": snapshot["counts"],
                            "database_version": built["database_version"], "profiles": profile_metadata,
                            "preparation": {"baseline_counts": baseline_counts, "previous_counts": previous_counts,
                                            "allow_count_decrease": allow_count_decrease},
                            "artifacts": {name: file_info(stage / name) for name in sorted(inventory(stage))}}
                write_json(stage / "release.json", manifest)
                (stage / "SHA256SUMS").write_text(checksums(stage, inventory(stage)), encoding="utf-8")
                result = verify_release(stage)
                # Recheck immutable inputs after all builders finish and before activation.
                load_snapshot(store, version)
                for name in inventory(stage):
                    with (stage / name).open("rb") as stream:
                        os.fsync(stream.fileno())
                stage.rename(destination)
        result["path"] = str(destination)
        atomic_json(pointer, {"release_version": release_version, "manifest": result["manifest"]})
        return result
