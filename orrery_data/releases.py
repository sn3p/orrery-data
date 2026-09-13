"""Prepare immutable, portable release candidates; never publish them."""

import gzip
import hashlib
from itertools import zip_longest
import math
import os
from datetime import timedelta
from pathlib import Path
import platform
import re
import shutil
import sqlite3
import tempfile
import zlib

from . import SCHEMA_VERSION, __version__
from .database import DATABASE_SCHEMA_VERSION, build_database, database_info, open_database
from .contracts import URL, identity_digest, validate_contract
from .metadata import validate_local_sources, validate_source_metadata
from .records import iter_master, validate_catalog_record
from .paths import validate_writable_path
from .formats import DataError, FIELDS
from .pipeline import (export, load_snapshot, refresh, validate_export_manifest, validate_snapshot_counts,
                       validate_snapshot_manifest)
from .storage import (URLS, atomic_json, file_info, now, read_json, utc_timestamp, validate_file_info, verify_file,
                      verify_generated_gzip,
                      write_json, writer_lock)

RELEASE_SCHEMA_VERSION = 1
COUNT_KEYS = ("orbital_records", "discovery_records", "master_records", "known_discovery")
EXPORT_FILES = {"master.jsonl.gz", "catalog.json", "catalog.json.gz", "MPCORB-header.txt", "NOTICE.txt"}
SHA256 = r"[a-f0-9]{64}"
COMMIT = r"[a-f0-9]{40}"
CLOCK_SKEW_SECONDS = 5


def require(condition, message):
    if not condition:
        raise DataError(message)


def validate_baseline(counts):
    if isinstance(counts, dict) and len(counts) == 9:
        validate_snapshot_counts(counts)
        counts = {key: counts[key] for key in COUNT_KEYS}
    require(isinstance(counts, dict) and set(counts) == set(COUNT_KEYS)
            and all(type(v) is int and v >= 0 for v in counts.values()),
            "Baseline counts require four nonnegative integers: " + ", ".join(COUNT_KEYS))
    return counts


def release_timestamp(value):
    return utc_timestamp(value, "Release generation")


def validate_release_metadata(manifest):
    keys = {"release_version", "release_schema_version", "identity", "dataset_identity", "created_at",
            "runtime", "sources", "counts", "database_version", "profiles", "preparation", "artifacts"}
    require(isinstance(manifest, dict) and set(manifest) == keys, "Invalid release manifest fields")
    require(all(isinstance(manifest[key], dict) for key in
                ("identity", "dataset_identity", "runtime", "sources", "counts", "profiles", "preparation", "artifacts")),
            "Release metadata fields must be objects")
    require(type(manifest["release_schema_version"]) is int, "Invalid release schema version type")
    created_at = release_timestamp(manifest["created_at"])
    runtime = manifest["runtime"]
    require(set(runtime) == {"python", "sqlite", "zlib"}
            and all(isinstance(value, str) and re.fullmatch(r"[0-9]+(?:\.[0-9]+)+[a-zA-Z0-9.+-]*", value)
                    for value in runtime.values()), "Invalid release runtime versions")
    validate_snapshot_counts(manifest["counts"])
    preparation = manifest["preparation"]
    require(set(preparation) == {"baseline_counts", "previous_counts", "allow_count_decrease"}
            and type(preparation["allow_count_decrease"]) is bool, "Invalid release preparation fields")
    if preparation["baseline_counts"] is not None:
        require(isinstance(preparation["baseline_counts"], dict) and set(preparation["baseline_counts"]) == set(COUNT_KEYS),
                "Invalid release baseline fields")
        validate_baseline(preparation["baseline_counts"])
    if preparation["previous_counts"] is not None:
        validate_snapshot_counts(preparation["previous_counts"])
    if not preparation["allow_count_decrease"]:
        for baseline in (preparation["baseline_counts"], preparation["previous_counts"]):
            if baseline is not None:
                require(all(manifest["counts"][key] >= baseline[key] for key in COUNT_KEYS),
                        "Release counts decreased without a recorded override")
    return created_at


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
            require(path.relative_to(directory).as_posix() == "exports"
                    or (path.parent == directory / "exports"
                        and (path.name == "full" or re.fullmatch(r"first-[1-9][0-9]*", path.name))),
                    "Unexpected bundle directory")
            continue
        require(path.is_file(), "Bundle must contain only regular files")
        result.add(path.relative_to(directory).as_posix())
    return result


def checksums(directory, names):
    return "".join(f"{file_info(directory / name)['sha256']}  {name}\n" for name in sorted(names))


def verify_catalog(directory, manifest, expected_records, connection, limit):
    plain = directory / "catalog.json"
    try:
        verify_generated_gzip(directory / "catalog.json.gz")
        with gzip.open(directory / "catalog.json.gz", "rb") as stream:
            require(hashlib.file_digest(stream, "sha256").hexdigest() == file_info(plain)["sha256"],
                    "Compressed catalog differs from JSON")
    except (OSError, EOFError, zlib.error) as exc:
        raise DataError(f"Invalid catalog.json.gz: {exc}") from exc
    rows = read_json(plain)
    require(isinstance(rows, list) and len(rows) == expected_records, "Catalog count mismatch")
    previous = -math.inf
    # Select in original MPCORB order BEFORE sorting. This projection is
    # independent of the exporter's serializer and preserves ties explicitly.
    expected = connection.execute("""SELECT disc, epoch, a, e, i, ascending_node, perihelion_argument, M, n
        FROM (SELECT o.source_order, o.disc, r.epoch, r.a, r.e, r.i, r.ascending_node,
                     r.perihelion_argument, r.M, r.n
              FROM objects o JOIN orbits r USING (source_order) WHERE o.disc IS NOT NULL
              ORDER BY o.source_order LIMIT ?)
        ORDER BY disc, source_order""", (-1 if limit is None else min(limit, expected_records),))
    for index, (row, expected_row) in enumerate(zip_longest(rows, expected), 1):
        validate_catalog_record(row)
        require(row["disc"] >= previous, "Catalog discovery order mismatch")
        previous = row["disc"]
        require(expected_row is not None and tuple(row[key] for key in FIELDS) == expected_row,
                f"Catalog row {index} differs from selected master/SQLite data")
    for name in ("catalog.json", "catalog.json.gz"):
        require(manifest["artifacts"][name]["records"] == expected_records
                and manifest["artifacts"][name]["profile"] == "discovery", "Catalog metadata mismatch")


def verify_master_database(directory, snapshot, connection):
    # Explicit SQL/JSON mapping deliberately independent of insert_master.
    keys = ("id", "number", "packed_designation", "readable_designation", "disc", "epoch",
            "a", "e", "i", "W", "w", "M", "n", "orbit_reference", "orbit_computer")
    rows = connection.execute("""SELECT o.source_order, o.id, o.number, o.packed_designation,
        o.readable_designation, o.disc, r.epoch, r.a, r.e, r.i, r.ascending_node,
        r.perihelion_argument, r.M, r.n, r.orbit_reference, r.orbit_computer
        FROM objects o JOIN orbits r USING (source_order) ORDER BY o.source_order""")
    total = known = numbered = 0
    for total, (master, sql) in enumerate(zip_longest(iter_master(directory / "exports/full/master.jsonl.gz"), rows), 1):
        require(master is not None and sql is not None and sql[0] == total
                and tuple(master[key] for key in keys) == sql[1:],
                f"SQLite row {total} differs from master data/source order")
        known += master["disc"] is not None
        numbered += master["number"] is not None
    counts = snapshot["counts"]
    require((total, known, total - known) == (counts["master_records"], counts["known_discovery"], counts["missing_discovery"]),
            "Master contents do not match snapshot counts")
    require(numbered <= counts["numbered_orbits"] and total - numbered <= counts["unnumbered_orbits"],
            "Snapshot numbered/unnumbered counts do not cover master records")


def verify_release(directory, manifest_sha256=None):
    """Verify a standalone copy without a producer checkout, source store or network."""
    directory = Path(directory)
    actual_files = inventory(directory)
    require({"release.json", "SHA256SUMS"} <= actual_files,
            "Bundle file inventory mismatch: release.json and SHA256SUMS are required")
    if manifest_sha256 is not None:
        require(isinstance(manifest_sha256, str) and re.fullmatch(SHA256, manifest_sha256),
                "Expected manifest SHA-256 must be 64 lowercase hexadecimal characters")
        require(file_info(directory / "release.json")["sha256"] == manifest_sha256,
                "Release manifest checksum mismatch")
    try:
        manifest = read_json(directory / "release.json")
        created_at = validate_release_metadata(manifest)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise DataError(f"release.json: {exc}") from exc
    identity = manifest["identity"]
    require(set(identity) == {"release_schema_version", "dataset_version", "snapshot_version", "producer",
                              "json_schema_version", "database_schema_version", "selected_limits", "notice_sha256"},
            "Invalid release identity fields")
    producer = identity["producer"]
    require(isinstance(producer, dict) and set(producer) == {"commit", "tool_version"},
            "Invalid release producer fields")
    require(all(type(identity[key]) is int for key in
                ("release_schema_version", "json_schema_version", "database_schema_version")),
            "Invalid identity schema version types")
    require(manifest["release_schema_version"] == RELEASE_SCHEMA_VERSION
            and identity["release_schema_version"] == RELEASE_SCHEMA_VERSION,
            "Unsupported release schema")
    require(manifest["release_version"] == "release-v1-" + identity_digest("release", identity), "Release identity mismatch")
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
    require({p.relative_to(directory).as_posix() for p in directory.rglob("*") if p.is_dir()}
            == {"exports", *(f"exports/{profile}" for profile in profiles)}, "Bundle directory inventory mismatch")
    for name in sorted(expected):
        require(isinstance(manifest["artifacts"][name], dict) and set(manifest["artifacts"][name]) == {"sha256", "bytes"},
                f"Invalid release artifact metadata fields: {name}")
        verify_file(directory / name, manifest["artifacts"][name])
    require((directory / "SHA256SUMS").read_text() == checksums(directory, expected | {"release.json"}),
            "Release SHA256SUMS mismatch")
    try:
        snapshot = read_json(directory / "snapshot.json")
        validate_snapshot_manifest(snapshot, identity["snapshot_version"])
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise DataError(f"snapshot.json: {exc}") from exc
    data_identity = dataset_identity(snapshot)
    require(manifest["dataset_identity"] == data_identity
            and identity["dataset_version"] == "data-v1-" + identity_digest("dataset", data_identity), "Dataset identity mismatch")
    require(manifest["counts"] == snapshot["counts"] and manifest["sources"] == snapshot["sources"],
            "Release source/count mismatch")
    verify_file(directory / "MPCORB-header.txt", snapshot["files"]["MPCORB-header.txt"])
    require(file_info(directory / "NOTICE.txt")["sha256"] == identity["notice_sha256"], "Attribution mismatch")
    database = database_info(directory / "orrery.sqlite3", verify=True)
    require(manifest["runtime"]["sqlite"] == database["sqlite_version"], "Release SQLite runtime mismatch")
    require(release_timestamp(database["created_at"]) <= created_at + timedelta(seconds=CLOCK_SKEW_SECONDS),
            "Release predates database generation beyond the 5-second clock tolerance; check the system clock")
    require(database["snapshot"] == snapshot and database["tool_version"] == producer["tool_version"]
            and database["database_version"] == manifest["database_version"]
            and database["mpcorb_header"] == (directory / "MPCORB-header.txt").read_text(encoding="utf-8")
            and database["notice"] == (directory / "NOTICE.txt").read_text(encoding="utf-8"),
            "Database release provenance/version mismatch")
    require(set(manifest["profiles"]) == set(profiles), "Release profile mismatch")
    validate_contract("release", manifest)
    with open_database(directory / "orrery.sqlite3") as (connection, _):
        verify_master_database(directory, snapshot, connection)
    for profile, limit in profiles.items():
        root = directory / "exports" / profile
        try:
            exported = read_json(root / "manifest.json")
            validate_export_manifest(exported)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise DataError(f"exports/{profile}/manifest.json: {exc}") from exc
        require(exported["compression"]["master"] == snapshot["compression"],
                f"exports/{profile}: master compression differs from snapshot")
        require(exported["compression"]["catalog"]["zlib"] == manifest["runtime"]["zlib"],
                "Release export zlib runtime mismatch")
        require(release_timestamp(exported["created_at"]) <= created_at + timedelta(seconds=CLOCK_SKEW_SECONDS),
                "Release predates export generation beyond the 5-second clock tolerance; check the system clock")
        expected_identity = {"snapshot_version": snapshot["snapshot_version"],
                             "tool_version": producer["tool_version"], "schema_version": SCHEMA_VERSION,
                             "selection": selection(limit)}
        count = min(limit, snapshot["counts"]["known_discovery"]) if limit else snapshot["counts"]["known_discovery"]
        require(exported["identity"] == expected_identity
                and exported["data_version"] == "export-v1-" + identity_digest("export", expected_identity)
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
        try:
            with open_database(directory / "orrery.sqlite3") as (connection, _):
                verify_catalog(root, exported, count, connection, limit)
        except (OSError, ValueError, KeyError, TypeError, EOFError) as exc:
            raise DataError(f"exports/{profile}: {exc}") from exc
    return {"status": "verified", "release_version": manifest["release_version"],
            "dataset_version": identity["dataset_version"], "snapshot_version": identity["snapshot_version"],
            "producer": producer, "counts": manifest["counts"], "profiles": manifest["profiles"],
            "manifest": file_info(directory / "release.json"), "path": str(directory)}


def prepare_release(store, output, producer_commit, *, version=None, limits=None, urls=None,
                    local=None, metadata=None, timeout=None, allow_count_decrease=False, baseline_counts=None):
    store = Path(store)
    require(version is None or (isinstance(version, str) and re.fullmatch(r"snapshot-v1-" + SHA256, version)),
            "Snapshot pin must be snapshot-v1- followed by 64 lowercase hexadecimal characters")
    require(isinstance(producer_commit, str) and re.fullmatch(COMMIT, producer_commit),
            "Producer commit must be a full 40-character lowercase Git SHA")
    require(limits is None or (isinstance(limits, list) and limits
                              and all(type(value) is int and value > 0 for value in limits)),
            "Selected limits must be positive integers")
    require(type(allow_count_decrease) is bool, "Count decrease override must be boolean")
    require(timeout is None or (type(timeout) is int and timeout > 0), "Timeout must be a positive integer")
    local = {} if local is None else local
    metadata = {} if metadata is None else metadata
    validate_local_sources(local)
    validate_source_metadata(metadata)
    require(version is None or (all(path is None for path in local.values()) and not metadata and timeout is None
                               and (urls is None or urls == URLS)),
            "Snapshot pin cannot be combined with source acquisition options")
    timeout = 60 if timeout is None else timeout
    limits = sorted(set(limits if limits is not None else [100000]))
    profiles = selections(limits)
    if baseline_counts is not None:
        baseline_counts = validate_baseline(baseline_counts)
    urls = URLS if urls is None else urls
    require(isinstance(urls, dict) and urls.keys() == URLS.keys(), "Source URLs require mpcorb and numbered")
    for name, value in urls.items():
        URL(value, f"Source URL {name}")
    if version is None:
        validate_writable_path(store, label="Source store")
        validate_writable_path(store / "snapshots", label="Snapshot output")
    output = Path(output).absolute()
    resolved_output = output.resolve()
    require(not output.is_symlink() and not resolved_output.is_relative_to((store / "snapshots").resolve()),
            "Release output must not be a symlink or be inside immutable snapshots")
    require(not any(parent.is_dir() and (re.fullmatch(r"release-v1-" + SHA256, parent.name)
                                        or (parent / "release.json").exists()
                                        or (parent / "release.json").is_symlink())
                    for parent in (resolved_output, *resolved_output.parents)),
            "Release output must not be at or inside an existing release candidate")
    require(version is not None or resolved_output != store.resolve(),
            "Source store and release output must be different directories when refreshing")
    validate_writable_path(output, label="Release output", protected_roots=(store / "snapshots",))
    with writer_lock(output):
        previous_counts = None
        previous_result = None
        previous = None
        pointer = output / "latest.json"
        if pointer.exists() or pointer.is_symlink():
            previous_pointer = read_json(pointer)
            require(isinstance(previous_pointer, dict) and set(previous_pointer) == {"release_version", "manifest"},
                    "Invalid latest release pointer: expected release_version and manifest; use a separate release output root")
            require(isinstance(previous_pointer["manifest"], dict) and set(previous_pointer["manifest"]) == {"sha256", "bytes"},
                    "Invalid latest release pointer manifest fields")
            validate_file_info(previous_pointer["manifest"], "latest.json manifest")
            previous = previous_pointer["release_version"]
            require(isinstance(previous, str) and re.fullmatch(r"release-v1-" + SHA256, previous),
                    "Invalid latest release pointer")
            try:
                verify_file(output / previous / "release.json", previous_pointer["manifest"])
                previous_result = verify_release(output / previous, previous_pointer["manifest"]["sha256"])
                require(previous_result["release_version"] == previous, "Previous release identity differs from its pointer")
            except (OSError, ValueError, KeyError, TypeError) as exc:
                raise DataError(f"Previous release referenced by latest.json ({previous}): {exc}. "
                                "Restore that bundle or use a fresh output root with inspected --baseline-counts") from exc
            previous_counts = previous_result["counts"]
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
                    "dataset_version": "data-v1-" + identity_digest("dataset", data_identity), "snapshot_version": version,
                    "producer": {"commit": producer_commit, "tool_version": __version__},
                    "json_schema_version": SCHEMA_VERSION, "database_schema_version": DATABASE_SCHEMA_VERSION,
                    "selected_limits": limits, "notice_sha256": file_info(Path(__file__).with_name("NOTICE.txt"))["sha256"]}
        release_version = "release-v1-" + identity_digest("release", identity)
        destination = output / release_version
        if destination.exists():
            result = previous_result if previous == release_version else verify_release(destination)
            require(result["release_version"] == release_version
                    and read_json(destination / "release.json")["identity"] == identity, "Existing release identity mismatch")
        else:
            with tempfile.TemporaryDirectory(prefix=".release-", dir=output) as temp:
                stage = Path(temp)
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
                write_json(stage / "snapshot.json", snapshot)
                manifest = {"release_version": release_version, "release_schema_version": RELEASE_SCHEMA_VERSION,
                            "identity": identity, "dataset_identity": data_identity, "created_at": now(),
                            "runtime": {"python": platform.python_version(), "sqlite": sqlite3.sqlite_version,
                                        "zlib": zlib.ZLIB_RUNTIME_VERSION},
                            "sources": snapshot["sources"], "counts": snapshot["counts"],
                            "database_version": built["database_version"], "profiles": profile_metadata,
                            "preparation": {"baseline_counts": baseline_counts, "previous_counts": previous_counts,
                                            "allow_count_decrease": allow_count_decrease},
                            "artifacts": {name: file_info(stage / name) for name in sorted(inventory(stage))}}
                write_json(stage / "release.json", manifest)
                (stage / "SHA256SUMS").write_text(checksums(stage, inventory(stage)), encoding="utf-8")
                result = verify_release(stage)
                # Recheck immutable inputs after all builders finish and before activation.
                require(load_snapshot(store, version)[1] == snapshot, "Snapshot changed during preparation")
                for name in inventory(stage):
                    with (stage / name).open("rb") as stream:
                        os.fsync(stream.fileno())
                stage.rename(destination)
        result["path"] = str(destination)
        atomic_json(pointer, {"release_version": release_version, "manifest": result["manifest"]})
        return result
