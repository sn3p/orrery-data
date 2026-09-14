"""Complete snapshots, read-only checks, and versioned static exports."""

from collections import Counter
import gzip
import hashlib
import http.client
from pathlib import Path
import re
import shutil
import tempfile
import zlib

from . import SCHEMA_VERSION, __version__
from .formats import DataError, FIELDS, discoveries, master_rows
from .contracts import URL, identity_digest, validate_contract
from .metadata import validate_local_sources, validate_source_metadata
from .records import iter_master, validate_catalog_record
from .paths import validate_flat_inventory, validate_writable_path
from .source_cache import cached_source, save_sources
from .storage import (URLS, acquire, atomic_json, deterministic_gzip, encode,
                      file_info, now, read_json, read_pointer, request, response_metadata,
                      utc_timestamp, validate_file_info, verify_decoded_source, verify_file,
                      verify_generated_gzip, write_json, writer_lock)

SNAPSHOT_FILES = {"snapshot.json", "mpcorb.input", "numbered.input", "master.jsonl.gz", "MPCORB-header.txt"}
EXPORT_FILES = {"master.jsonl.gz", "catalog.json", "catalog.json.gz", "MPCORB-header.txt", "NOTICE.txt"}


def current(store):
    pointer = store / "current.json"
    if not pointer.exists() and not pointer.is_symlink():
        return None
    value = read_pointer(pointer)
    if (not isinstance(value, dict) or set(value) != {"snapshot_version"}
            or not isinstance(value["snapshot_version"], str)
            or not re.fullmatch(r"snapshot-v1-[a-f0-9]{64}", value["snapshot_version"])):
        raise DataError("Invalid current snapshot pointer")
    return value["snapshot_version"]


def load_snapshot(store, version=None, verify=True):
    version = current(store) if version is None else version
    if not isinstance(version, str) or not re.fullmatch(r"snapshot-v1-[a-f0-9]{64}", version):
        raise DataError("No valid snapshot selected; run refresh first")
    directory = store / "snapshots" / version
    validate_flat_inventory(directory, SNAPSHOT_FILES, label="Snapshot")
    manifest = read_json(directory / "snapshot.json")
    validate_snapshot_manifest(manifest, version)
    if verify:
        for name, info in manifest["sources"].items():
            if name not in ("mpcorb", "numbered"):
                raise DataError("Unexpected snapshot source")
            verify_file(directory / f"{name}.input", info)
            verify_decoded_source(directory / f"{name}.input", info)
        for name in ("master.jsonl.gz", "MPCORB-header.txt"):
            verify_file(directory / name, manifest["files"][name])
        verify_generated_gzip(directory / "master.jsonl.gz")
    return directory, manifest


def validate_snapshot_manifest(manifest, version):
    keys = {"snapshot_version", "identity", "schema_version", "tool_version", "created_at",
            "sources", "counts", "files", "exclusions", "compression"}
    if not isinstance(manifest, dict) or set(manifest) != keys:
        raise DataError("Invalid snapshot manifest fields")
    utc_timestamp(manifest["created_at"], "Snapshot generation")
    identity = manifest["identity"]
    if (not isinstance(identity, dict) or set(identity) != {"schema_version", "tool_version", "sources"}
            or not isinstance(identity["sources"], dict)
            or type(identity["schema_version"]) is not int or type(manifest["schema_version"]) is not int):
        raise DataError("Invalid snapshot identity fields")
    if (manifest["snapshot_version"] != version
            or "snapshot-v1-" + identity_digest("snapshot", manifest["identity"]) != version
            or manifest["schema_version"] != SCHEMA_VERSION
            or manifest["identity"]["schema_version"] != manifest["schema_version"]
            or manifest["identity"]["tool_version"] != manifest["tool_version"]):
        raise DataError("Snapshot identity/schema mismatch")
    if not isinstance(manifest["sources"], dict) or set(manifest["sources"]) != {"mpcorb", "numbered"}:
        raise DataError("Snapshot requires both mpcorb and numbered sources")
    for name, info in manifest["sources"].items():
        validate_file_info(info, f"snapshot source {name}")
        validate_file_info(info.get("decoded"), f"snapshot source {name}.decoded")
    if manifest["identity"]["sources"] != {name: info["decoded"]["sha256"] for name, info in manifest["sources"].items()}:
        raise DataError("Snapshot source identity mismatch")
    if not isinstance(manifest["files"], dict) or set(manifest["files"]) != {"master.jsonl.gz", "MPCORB-header.txt"}:
        raise DataError("Snapshot is missing required files")
    for name, info in manifest["files"].items():
        validate_file_info(info, f"snapshot file {name}")
    validate_snapshot_counts(manifest["counts"])
    counts = manifest["counts"]
    if manifest["exclusions"] != {"master": {"non_elliptic_orbits": counts["unsupported_orbits"]},
                                  "discovery": {"missing_discovery_date": counts["missing_discovery"]}}:
        raise DataError("Invalid snapshot exclusions")
    validate_compression(manifest["compression"], "snapshot")
    validate_contract("snapshot", manifest)


def validate_compression(value, label):
    if (not isinstance(value, dict) or set(value) != {"format", "level", "mtime", "zlib"}
            or value["format"] != "gzip" or type(value["level"]) is not int or value["level"] != 6
            or type(value["mtime"]) is not int or value["mtime"] != 0
            or not isinstance(value["zlib"], str) or not re.fullmatch(r"[0-9]+(?:\.[0-9]+)+[a-zA-Z0-9.+-]*", value["zlib"])):
        raise DataError(f"Invalid {label} compression metadata")


def validate_snapshot_counts(counts):
    keys = {"orbital_records", "master_records", "known_discovery", "missing_discovery",
            "numbered_orbits", "unnumbered_orbits", "unsupported_orbits",
            "discovery_records", "unmatched_discovery_records"}
    if (not isinstance(counts, dict) or set(counts) != keys
            or not all(type(value) is int and value >= 0 for value in counts.values())):
        raise DataError("Invalid snapshot count fields: expected nine nonnegative integers")
    if (counts["master_records"] + counts["unsupported_orbits"] != counts["orbital_records"]
            or counts["known_discovery"] + counts["missing_discovery"] != counts["master_records"]
            or counts["numbered_orbits"] + counts["unnumbered_orbits"] != counts["orbital_records"]
            or counts["known_discovery"] + counts["unmatched_discovery_records"] != counts["discovery_records"]
            or counts["known_discovery"] > counts["numbered_orbits"]):
        raise DataError("Snapshot counts do not reconcile")


def validate_export_manifest(manifest):
    keys = {"data_version", "identity", "snapshot_version", "schema_version", "tool_version", "created_at",
            "selection", "sources", "counts", "exclusions", "compression", "artifacts"}
    if (not isinstance(manifest, dict) or set(manifest) != keys
            or not all(isinstance(manifest[key], dict) for key in
                       ("identity", "selection", "sources", "counts", "exclusions", "compression", "artifacts"))):
        raise DataError("Invalid export manifest fields")
    identity = manifest["identity"]
    if (set(identity) != {"snapshot_version", "tool_version", "schema_version", "selection"}
            or type(identity["schema_version"]) is not int or type(manifest["schema_version"]) is not int
            or not all(isinstance(manifest[key], str) and manifest[key] for key in
                       ("data_version", "snapshot_version", "tool_version", "created_at"))):
        raise DataError("Invalid export identity fields")
    selected = manifest["selection"]
    if (set(selected) != {"profile", "limit", "select", "sort"}
            or (selected["limit"] is not None and (type(selected["limit"]) is not int or selected["limit"] <= 0))
            or selected["profile"] != "discovery" or selected["select"] != "first-known-dates-in-mpcorb-order"
            or selected["sort"] != "disc-ascending-stable"):
        raise DataError("Invalid export selection fields")
    counts = manifest["counts"]
    if type(counts.get("discovery_export")) is not int or counts["discovery_export"] < 0:
        raise DataError("Invalid export discovery count")
    validate_snapshot_counts({key: value for key, value in counts.items() if key != "discovery_export"})
    expected_count = min(selected["limit"], counts["known_discovery"]) if selected["limit"] else counts["known_discovery"]
    if counts["discovery_export"] != expected_count:
        raise DataError("Export discovery count does not match selection")
    if set(manifest["sources"]) != {"mpcorb", "numbered"}:
        raise DataError("Invalid export sources")
    for name, info in manifest["sources"].items():
        validate_file_info(info, f"export source {name}")
        validate_file_info(info.get("decoded"), f"export source {name}.decoded")
    if (manifest["exclusions"] != {"master": {"non_elliptic_orbits": counts["unsupported_orbits"]},
                                   "discovery": {"missing_discovery_date": counts["missing_discovery"]},
                                   "selection_limit": counts["known_discovery"] - counts["discovery_export"]}):
        raise DataError("Invalid export exclusions")
    compression = manifest["compression"]
    if set(compression) != {"master", "catalog"}:
        raise DataError("Invalid export compression fields")
    for name, value in compression.items():
        validate_compression(value, f"export {name}")
    names = {"master.jsonl.gz", "catalog.json", "catalog.json.gz", "MPCORB-header.txt", "NOTICE.txt"}
    if set(manifest["artifacts"]) != names:
        raise DataError("Invalid export artifact fields")
    for name, info in manifest["artifacts"].items():
        validate_file_info(info, f"export artifact {name}")
        if name in ("master.jsonl.gz", "catalog.json", "catalog.json.gz"):
            profile, records = ("master", counts["master_records"]) if name == "master.jsonl.gz" else ("discovery", expected_count)
            if (set(info) != {"sha256", "bytes", "profile", "records"} or type(info["records"]) is not int
                    or info["records"] != records or info["profile"] != profile):
                raise DataError(f"Invalid export artifact record metadata: {name}")
        elif set(info) != {"sha256", "bytes"}:
            raise DataError(f"Invalid export artifact fields: {name}")
    validate_contract("export", manifest)
    if (manifest["identity"] != {"snapshot_version": manifest["snapshot_version"],
                                 "tool_version": manifest["tool_version"], "schema_version": SCHEMA_VERSION,
                                 "selection": manifest["selection"]}
            or manifest["data_version"] != "export-v1-" + identity_digest("export", manifest["identity"])):
        raise DataError("Export identity/schema mismatch")


def check(store, urls, timeout):
    baseline = load_snapshot(store, verify=False)[1] if current(store) else None
    results = {}
    for name, url in urls.items():
        row = {"url": url, "status": "unknown"}
        old = baseline["sources"][name] if baseline else None
        try:
            with request(url, method="HEAD", timeout=timeout) as response:
                if response.status != 200:
                    raise DataError(f"Expected HTTP 200, got {response.status}")
                row.update(response_metadata(response))
            if not old or old["url"] != url:
                row["reason"] = "no baseline for this URL"
            elif row["etag"] and old["etag"]:
                if row["etag"] != old["etag"]:
                    row.update(status="changed", reason="ETag changed")
                elif not row["etag"].startswith("W/"):
                    row.update(status="unchanged", reason="strong ETag matches (server validator, not a download hash)")
                else:
                    row["reason"] = "weak ETag cannot establish byte identity"
            elif row["last_modified"] and old["last_modified"] and row["last_modified"] != old["last_modified"]:
                row.update(status="changed", reason="Last-Modified changed")
            elif (row["content_length"] is not None and old["content_length"] is not None
                  and int(row["content_length"]) != int(old["content_length"])):
                row.update(status="changed", reason="Content-Length changed")
            else:
                row["reason"] = "no comparable strong validator; refresh to compare content hashes"
        except (OSError, ValueError, http.client.HTTPException) as exc:
            row["reason"] = str(exc)
        results[name] = row
    states = {row["status"] for row in results.values()}
    return {"checked_at": now(), "snapshot_version": baseline["snapshot_version"] if baseline else None,
            "status": "changed" if "changed" in states else "unknown" if "unknown" in states else "unchanged",
            "sources": results}


def refresh(store, urls, local, metadata, timeout, allow_count_decrease=False, reuse_unchanged=False):
    store = Path(store)
    if not isinstance(urls, dict) or urls.keys() != URLS.keys():
        raise DataError("Source URLs require mpcorb and numbered")
    for name, url in urls.items():
        URL(url, f"Source URL {name}")
    validate_local_sources(local)
    validate_source_metadata(metadata)
    if type(timeout) is not int or timeout <= 0:
        raise DataError("Timeout must be a positive integer")
    if type(allow_count_decrease) is not bool:
        raise DataError("Count decrease override must be boolean")
    if type(reuse_unchanged) is not bool:
        raise DataError("Source reuse must be boolean")
    validate_writable_path(store, label="Source store")
    validate_writable_path(store / "snapshots", label="Snapshot output")
    with writer_lock(store):
        previous = load_snapshot(store)[1] if current(store) else None
        snapshots = store / "snapshots"
        snapshots.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(prefix=".refresh-", dir=store) as temp:
            stage = Path(temp)
            cache = store / "http-cache"
            sources = {name: acquire(name, stage, url, local.get(name), metadata.get(name, {}), timeout,
                                    cached_source(cache, name, url) if reuse_unchanged and not local.get(name) else None)
                       for name, url in urls.items()}
            identity = {"schema_version": SCHEMA_VERSION, "tool_version": __version__,
                        "sources": {name: info["decoded"]["sha256"] for name, info in sources.items()}}
            if reuse_unchanged and previous and identity == previous["identity"]:
                destination = snapshots / previous["snapshot_version"]
                save_sources(cache, stage, sources)
                return {"status": "unchanged", "snapshot_version": previous["snapshot_version"],
                        "counts": previous["counts"], "path": str(destination)}
            discovery = discoveries(stage / "numbered.txt")
            counts, header = Counter(), []
            with deterministic_gzip(stage / "master.jsonl.gz") as target:
                for record in master_rows(stage / "mpcorb.txt", discovery, counts, header):
                    target.write((encode(record) + "\n").encode())
            (stage / "MPCORB-header.txt").write_text("".join(header), encoding="utf-8")
            if previous and not allow_count_decrease:
                for key in ("orbital_records", "discovery_records", "master_records", "known_discovery"):
                    if counts[key] < previous["counts"][key]:
                        raise DataError(f"{key} decreased from {previous['counts'][key]} to {counts[key]}; "
                                        "inspect sources, then explicitly use --allow-count-decrease if intentional")
            version = "snapshot-v1-" + identity_digest("snapshot", identity)
            manifest = {"snapshot_version": version, "identity": identity,
                        "schema_version": SCHEMA_VERSION, "tool_version": __version__,
                        "created_at": now(), "sources": sources, "counts": dict(counts),
                        "files": {name: file_info(stage / name) for name in ("master.jsonl.gz", "MPCORB-header.txt")},
                        "exclusions": {"master": {"non_elliptic_orbits": counts["unsupported_orbits"]},
                                       "discovery": {"missing_discovery_date": counts["missing_discovery"]}},
                        "compression": {"format": "gzip", "level": 6, "mtime": 0, "zlib": zlib.ZLIB_RUNTIME_VERSION}}
            validate_snapshot_manifest(manifest, version)
            write_json(stage / "snapshot.json", manifest)
            for name in urls:
                (stage / f"{name}.txt").unlink()
            destination = snapshots / version
            if destination.exists() or destination.is_symlink():
                # First acquisition wins; retries cannot rewrite immutable provenance.
                _, manifest = load_snapshot(store, version)
            else:
                stage.rename(destination)
            if reuse_unchanged:
                save_sources(cache, destination, manifest["sources"])
            atomic_json(store / "current.json", {"snapshot_version": version})
        return {"status": "unchanged" if previous and previous["snapshot_version"] == version else "updated",
                "snapshot_version": version, "counts": manifest["counts"], "path": str(destination)}


def selected_master(path, counts, limit):
    selected = []
    total = known = 0
    for row in iter_master(path):
        total += 1
        known += row["disc"] is not None
        if row["disc"] is not None and (limit is None or len(selected) < limit):
            selected.append(tuple(row[key] for key in FIELDS))
    if (total, known, total - known) != tuple(counts[key] for key in
                                             ("master_records", "known_discovery", "missing_discovery")):
        raise DataError("Master contents do not match snapshot counts")
    selected.sort(key=lambda row: row[0])
    return selected


def validate_export_pointer(output):
    pointer = output / "latest.json"
    if pointer.exists() or pointer.is_symlink():
        value = read_pointer(pointer)
        if (not isinstance(value, dict) or set(value) != {"data_version"}
                or not isinstance(value["data_version"], str)
                or not re.fullmatch(r"export-v1-[a-f0-9]{64}", value["data_version"])):
            raise DataError("Invalid latest export pointer; use a separate export output root")


def export(store, output, version=None, limit=None):
    if limit is not None and (type(limit) is not int or limit <= 0):
        raise DataError("--limit must be a positive integer")
    validate_writable_path(output, label="Export output", protected_roots=(store / "snapshots",))
    validate_export_pointer(output)
    source, snapshot = load_snapshot(store, version)
    selection = {"profile": "discovery", "limit": limit,
                 "select": "first-known-dates-in-mpcorb-order", "sort": "disc-ascending-stable"}
    identity = {"snapshot_version": snapshot["snapshot_version"], "tool_version": __version__,
                "schema_version": SCHEMA_VERSION, "selection": selection}
    export_version = "export-v1-" + identity_digest("export", identity)
    with writer_lock(output):
        validate_export_pointer(output)  # Recheck after excluding cooperating writers.
        destination = output / export_version
        if destination.exists() or destination.is_symlink():
            validate_flat_inventory(destination, EXPORT_FILES | {"manifest.json", "SHA256SUMS"}, label="Export")
            manifest = read_json(destination / "manifest.json")
            validate_export_manifest(manifest)
            if manifest["identity"] != identity or manifest["data_version"] != export_version:
                raise DataError("Existing export identity mismatch")
            if (manifest["sources"] != snapshot["sources"]
                    or manifest["compression"]["master"] != snapshot["compression"]
                    or {key: value for key, value in manifest["counts"].items() if key != "discovery_export"} != snapshot["counts"]):
                raise DataError("Existing export snapshot provenance mismatch")
            names = EXPORT_FILES
            if set(manifest["artifacts"]) != names:
                raise DataError("Existing export is missing required artifacts")
            for name, info in manifest["artifacts"].items():
                verify_file(destination / name, info)
            for name, info in snapshot["files"].items():
                verify_file(destination / name, info)
            verify_file(destination / "NOTICE.txt", file_info(Path(__file__).with_name("NOTICE.txt")))
            selected = selected_master(source / "master.jsonl.gz", snapshot["counts"], limit)
            catalog = read_json(destination / "catalog.json")
            if not isinstance(catalog, list) or len(catalog) != len(selected):
                raise DataError("Existing export catalog differs from selected master data")
            for row, expected in zip(catalog, selected):
                validate_catalog_record(row)
                if tuple(row[key] for key in FIELDS) != expected:
                    raise DataError("Existing export catalog differs from selected master data")
            verify_generated_gzip(destination / "catalog.json.gz")
            with gzip.open(destination / "catalog.json.gz", "rb") as stream:
                if hashlib.file_digest(stream, "sha256").hexdigest() != file_info(destination / "catalog.json")["sha256"]:
                    raise DataError("Existing compressed catalog differs from JSON")
            expected_sums = "".join(f"{file_info(destination / name)['sha256']}  {name}\n"
                                    for name in sorted([*names, "manifest.json"]))
            if (destination / "SHA256SUMS").read_text() != expected_sums:
                raise DataError("Existing export SHA256SUMS mismatch")
        else:
            with tempfile.TemporaryDirectory(prefix=".export-", dir=output) as temp:
                stage = Path(temp)
                for name in ("master.jsonl.gz", "MPCORB-header.txt"):
                    shutil.copyfile(source / name, stage / name)
                shutil.copyfile(Path(__file__).with_name("NOTICE.txt"), stage / "NOTICE.txt")
                selected = selected_master(source / "master.jsonl.gz", snapshot["counts"], limit)
                with (stage / "catalog.json").open("wb") as catalog, deterministic_gzip(stage / "catalog.json.gz") as compressed:
                    def emit(data):
                        catalog.write(data)
                        compressed.write(data)
                    emit(b"[")
                    for index, row in enumerate(selected):
                        if index:
                            emit(b",")
                        emit(encode(dict(zip(FIELDS, row))).encode())
                    emit(b"]\n")
                artifacts = {name: file_info(stage / name) for name in (
                    "master.jsonl.gz", "catalog.json", "catalog.json.gz", "MPCORB-header.txt", "NOTICE.txt")}
                for name in ("catalog.json", "catalog.json.gz"):
                    artifacts[name].update(profile="discovery", records=len(selected))
                artifacts["master.jsonl.gz"].update(profile="master", records=snapshot["counts"]["master_records"])
                manifest = {"data_version": export_version, "identity": identity,
                            "snapshot_version": snapshot["snapshot_version"], "schema_version": SCHEMA_VERSION,
                            "tool_version": __version__, "created_at": now(), "selection": selection,
                            "sources": snapshot["sources"], "counts": {**snapshot["counts"], "discovery_export": len(selected)},
                            "exclusions": {**snapshot["exclusions"], "selection_limit": snapshot["counts"]["known_discovery"] - len(selected)},
                            "compression": {"master": snapshot["compression"],
                                            "catalog": {"format": "gzip", "level": 6, "mtime": 0, "zlib": zlib.ZLIB_RUNTIME_VERSION}},
                            "artifacts": artifacts}
                validate_export_manifest(manifest)
                write_json(stage / "manifest.json", manifest)
                # Convenient release-side checksum list includes the manifest itself.
                lines = [f"{file_info(stage / name)['sha256']}  {name}\n" for name in sorted([*artifacts, "manifest.json"])]
                (stage / "SHA256SUMS").write_text("".join(lines))
                for name, info in snapshot["files"].items():
                    verify_file(source / name, info)
                    verify_file(stage / name, info)
                stage.rename(destination)
        atomic_json(output / "latest.json", {"data_version": export_version})
    return {"data_version": export_version, "path": str(destination), "counts": manifest["counts"],
            "artifacts": manifest["artifacts"]}
