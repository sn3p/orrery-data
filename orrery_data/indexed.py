"""Optional, offline indexed derivatives of unchanged schema-1 exports."""

import gzip
import hashlib
import json
from pathlib import Path
import shutil
import stat
import tempfile

from . import __version__
from .contracts import VERSION
from .formats import DataError, FIELDS
from .paths import validate_disjoint_paths, validate_flat_inventory, validate_writable_path
from .pipeline import EXPORT_FILES, selected_master, validate_export_manifest
from .records import validate_catalog_record
from .storage import (deterministic_gzip, encode, file_info, read_json, verify_file,
                      verify_generated_gzip, write_json, writer_lock)

CONTRACT_VERSION = 1
DEFAULT_CHUNK_BYTES = 1024 * 1024
MAX_CHUNK_BYTES = 8 * 1024 * 1024
MAX_INDEX_BYTES = 4 * 1024 * 1024


def chunk_limit(value):
    if type(value) is not int or not 1 <= value <= MAX_CHUNK_BYTES:
        raise DataError(f"chunk_bytes must be an integer between 1 and {MAX_CHUNK_BYTES}")
    return value


def verify_source_export(directory):
    """Use the existing manifest/master contract; do not acquire new sources."""
    validate_flat_inventory(directory, EXPORT_FILES | {"manifest.json", "SHA256SUMS"}, label="Export")
    manifest = read_json(directory / "manifest.json")
    validate_export_manifest(manifest)
    for name, info in manifest["artifacts"].items():
        verify_file(directory / name, info)
    sums = "".join(f"{file_info(directory / name)['sha256']}  {name}\n"
                   for name in sorted([*EXPORT_FILES, "manifest.json"]))
    if (directory / "SHA256SUMS").read_text() != sums:
        raise DataError("Export SHA256SUMS mismatch")
    verify_generated_gzip(directory / "master.jsonl.gz")
    selected = selected_master(directory / "master.jsonl.gz", manifest["counts"], manifest["selection"]["limit"])
    rows = read_json(directory / "catalog.json")
    if not isinstance(rows, list) or len(rows) != len(selected):
        raise DataError("Export catalog differs from selected master data")
    for row, expected in zip(rows, selected):
        validate_catalog_record(row)
        if tuple(row[key] for key in FIELDS) != expected:
            raise DataError("Export catalog differs from selected master data")
    verify_compressed(directory / "catalog.json.gz", manifest["artifacts"]["catalog.json"])
    return manifest, rows


def verify_compressed(path, decoded):
    verify_generated_gzip(path)
    with gzip.open(path, "rb") as stream:
        sha = hashlib.sha256()
        size = 0
        while block := stream.read(1024 * 1024):
            sha.update(block)
            size += len(block)
    if {"sha256": sha.hexdigest(), "bytes": size} != {key: decoded[key] for key in ("sha256", "bytes")}:
        raise DataError(f"Compressed payload differs from JSON: {path.name}")


def chunk_records(rows, limit):
    """Bound actual encoded bytes, allowing a discovery date to span files."""
    start, parts, size = 0, [], 3  # '[', ']\n'
    for ordinal, row in enumerate(rows):
        part = encode({key: row[key] for key in FIELDS}).encode("utf-8")
        if len(part) + 3 > limit:
            raise DataError(f"Catalog record {ordinal} cannot fit within chunk_bytes={limit}")
        added = len(part) + bool(parts)
        if parts and size + added > limit:
            yield start, ordinal, b"[" + b",".join(parts) + b"]\n"
            start, parts, size = ordinal, [], 3
            added = len(part)
        parts.append(part)
        size += added
    if parts:
        yield start, len(rows), b"[" + b",".join(parts) + b"]\n"


def reference(directory, name):
    return {"url": name, **file_info(directory / name)}


def descriptor(directory, name):
    return {**reference(directory, name), "gzip": reference(directory, name + ".gz")}


def make_index(directory, manifest, rows, limit, producer_version, *, write=False):
    date_counts = []
    for ordinal, row in enumerate(rows, 1):
        if date_counts and date_counts[-1][0] == row["disc"]:
            date_counts[-1][1] = ordinal
        else:
            date_counts.append([row["disc"], ordinal])
    chunks, names = [], set()
    for number, (start, end, payload) in enumerate(chunk_records(rows, limit)):
        name = f"chunks/{number:06d}.json"
        names.update((Path(name).name, Path(name + ".gz").name))
        if write:
            (directory / name).write_bytes(payload)
            with deterministic_gzip(directory / (name + ".gz")) as compressed:
                compressed.write(payload)
        else:
            decoded = {"bytes": len(payload), "sha256": hashlib.sha256(payload).hexdigest()}
            verify_file(directory / name, decoded)
            verify_compressed(directory / (name + ".gz"), decoded)
        chunks.append({"start": start, "end": end, "first_disc": rows[start]["disc"],
                       "last_disc": rows[end - 1]["disc"], **descriptor(directory, name)})
    if names or (directory / "chunks").exists() or (directory / "chunks").is_symlink():
        validate_flat_inventory(directory / "chunks", names, label="Indexed chunks")
    return {"contract_version": CONTRACT_VERSION, "schema_version": 1, "encoding": "json-array",
            "catalog_id": manifest["data_version"], "snapshot_version": manifest["snapshot_version"],
            "producer": {"tool_version": producer_version},
            **{key: manifest[key] for key in ("selection", "counts", "exclusions", "sources")},
            "full": descriptor(directory, "full/catalog.json"),
            "provenance": {key: reference(directory, "full/" + name) for key, name in (
                ("manifest", "manifest.json"), ("master", "master.jsonl.gz"),
                ("header", "MPCORB-header.txt"), ("notice", "NOTICE.txt"))},
            "chunk_bytes": limit, "date_counts": date_counts, "chunks": chunks}


def verify_indexed(directory, index_sha256=None):
    directory = Path(directory)
    if not stat.S_ISDIR(directory.lstat().st_mode):
        raise DataError("Indexed bundle must be a real directory")
    if {p.name for p in directory.iterdir()} not in ({"index.json", "full"}, {"index.json", "full", "chunks"}):
        raise DataError("Indexed bundle inventory mismatch")
    path = directory / "index.json"
    if not stat.S_ISREG(path.lstat().st_mode) or path.stat().st_size > MAX_INDEX_BYTES:
        raise DataError("Index must be a regular file of at most 4 MiB")
    pin = file_info(path)
    if index_sha256 is not None and index_sha256 != pin["sha256"]:
        raise DataError("Index SHA-256 does not match trusted pin")
    index = read_json(path)
    if (not isinstance(index, dict) or type(index.get("contract_version")) is not int
            or index["contract_version"] != CONTRACT_VERSION
            or type(index.get("schema_version")) is not int or index["schema_version"] != 1
            or index.get("encoding") != "json-array"):
        raise DataError("Unsupported indexed contract, schema or encoding")
    limit = chunk_limit(index.get("chunk_bytes"))
    producer = index.get("producer")
    if not isinstance(producer, dict) or set(producer) != {"tool_version"}:
        raise DataError("Invalid indexed producer metadata")
    VERSION(producer["tool_version"], "Indexed producer tool_version")
    manifest, rows = verify_source_export(directory / "full")
    expected = make_index(directory, manifest, rows, limit, producer["tool_version"])
    # Compare JSON types as well as values (True and 1.0 are not integer counts),
    # while permitting object key reordering. No separate consumer schema parser.
    if json.dumps(index, sort_keys=True) != json.dumps(expected, sort_keys=True):
        raise DataError("Index metadata, date counts or chunk ranges differ from the catalog")
    return {"catalog_id": index["catalog_id"], "path": str(directory),
            "pin": {"url": "index.json", **pin}, "records": len(rows), "chunks": len(index["chunks"])}


def export_indexed(source, output, chunk_bytes=DEFAULT_CHUNK_BYTES):
    source, output = Path(source), Path(output)
    chunk_limit(chunk_bytes)
    validate_writable_path(output, label="Indexed output")
    validate_disjoint_paths(output, (source,), label="Indexed output")
    manifest, rows = verify_source_export(source)
    with writer_lock(output):
        with tempfile.TemporaryDirectory(prefix=".indexed-", dir=output) as temp:
            stage = Path(temp)
            shutil.copytree(source, stage / "full")
            if rows:
                (stage / "chunks").mkdir()
            index = make_index(stage, manifest, rows, chunk_bytes, __version__, write=True)
            del rows
            write_json(stage / "index.json", index)
            result = verify_indexed(stage)
            destination = output / ("delivery-v1-" + result["pin"]["sha256"])
            if destination.exists() or destination.is_symlink():
                result = verify_indexed(destination, result["pin"]["sha256"])
            else:
                stage.rename(destination)
            result["path"] = str(destination)
    return result
