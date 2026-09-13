"""Verified input acquisition and atomic, immutable directory installation."""

from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import gzip
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import tempfile
from urllib.request import Request, urlopen

from . import __version__
from .formats import DataError
from .paths import validate_append_path


URLS = {
    "mpcorb": "https://minorplanetcenter.net/iau/MPCORB/MPCORB.DAT.gz",
    "numbered": "https://minorplanetcenter.net/iau/lists/NumberedMPs.txt",
}
CHUNK = 1024 * 1024


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def utc_timestamp(value, label):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z", value):
        raise DataError(f"{label} timestamps must be UTC YYYY-MM-DDTHH:MM:SSZ")
    try:
        return datetime.fromisoformat(value)
    except ValueError as exc:
        raise DataError(f"Invalid {label.lower()} timestamp") from exc


def encode(value):
    return json.dumps(value, ensure_ascii=True, allow_nan=False, separators=(",", ":"))


def digest(value):
    return hashlib.sha256(encode(value).encode()).hexdigest()


def file_info(path):
    with path.open("rb") as stream:
        sha = hashlib.file_digest(stream, "sha256").hexdigest()
    return {"sha256": sha, "bytes": path.stat().st_size}


def validate_file_info(info, label):
    if (not isinstance(info, dict) or not {"sha256", "bytes"} <= info.keys()
            or not isinstance(info["sha256"], str) or not re.fullmatch(r"[a-f0-9]{64}", info["sha256"])
            or type(info["bytes"]) is not int or info["bytes"] < 0):
        raise DataError(f"Invalid file metadata: {label} requires sha256 and nonnegative integer bytes")


def verify_file(path, info):
    validate_file_info(info, path.name)
    if file_info(path) != {k: info[k] for k in ("sha256", "bytes")}:
        raise DataError(f"Checksum or size mismatch: {path.name}")


def verify_generated_gzip_header(path):
    # Generated artifacts have no optional header fields or stored filename,
    # and record mtime=0. Runtime and compression level cannot be inferred
    # reliably from the stream; source-input gzip has no such header contract.
    with path.open("rb") as stream:
        header = stream.read(10)
    if len(header) != 10 or header[:8] != b"\x1f\x8b\x08\x00\x00\x00\x00\x00":
        raise DataError(f"Invalid {path.name}: generated gzip requires zero mtime and no optional header fields")


def verify_decoded_source(path, info):
    sha, size = hashlib.sha256(), 0
    opener = gzip.open if info["compression"] == "gzip" else open
    with opener(path, "rb") as stream:
        while chunk := stream.read(CHUNK):
            sha.update(chunk)
            size += len(chunk)
    if {"sha256": sha.hexdigest(), "bytes": size} != info["decoded"]:
        raise DataError(f"Decoded source checksum or size mismatch: {path.name}")


def read_json(path):
    with path.open() as stream:
        return loads_json(stream.read())


def loads_json(text):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise DataError(f"Duplicate JSON key: {key}")
            result[key] = value
        return result

    def number(value):
        parsed = float(value)
        if not math.isfinite(parsed):
            raise DataError("JSON numbers must be finite")
        return parsed

    def constant(value):
        raise DataError(f"Invalid JSON constant: {value}")

    return json.loads(text, object_pairs_hook=pairs, parse_float=number, parse_constant=constant)


def write_json(path, value):
    path.write_text(encode(value) + "\n", encoding="utf-8")


def atomic_json(path, value):
    fd, name = tempfile.mkstemp(prefix=".pointer-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            stream.write(encode(value) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


@contextmanager
def writer_lock(root):
    validate_append_path(root / ".lock", label="Writer lock")
    root.mkdir(parents=True, exist_ok=True)
    with (root / ".lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise DataError(f"Another writer is using {root}") from exc
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


@contextmanager
def deterministic_gzip(path):
    with path.open("wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0, compresslevel=6) as compressed:
            yield compressed


def response_metadata(response):
    return {"etag": response.headers.get("ETag"),
            "last_modified": response.headers.get("Last-Modified"),
            "content_length": response.headers.get("Content-Length")}


def request(url, method="GET", timeout=60):
    if not url.startswith(("https://", "http://")):
        raise DataError("Source URLs must use HTTP or HTTPS")
    return urlopen(Request(url, method=method, headers={
        "User-Agent": f"orrery-data/{__version__}", "Accept-Encoding": "identity"}), timeout=timeout)


def acquire(name, stage, url, local, metadata, timeout):
    raw = stage / f"{name}.input"
    info = {"url": url, "retrieved_at": None if local else now(), "acquisition": "local" if local else "http",
            "etag": None, "last_modified": None, "content_length": None}
    if local:
        # Only explicit source metadata is retained; private local paths are not published.
        info.update({k: metadata[k] for k in ("etag", "last_modified", "content_length", "retrieved_at") if k in metadata})
        shutil.copyfile(local, raw)
    else:
        with request(url, timeout=timeout) as response, raw.open("wb") as target:
            if response.status != 200:
                raise DataError(f"{name}: expected HTTP 200, got {response.status}")
            if response.headers.get("Content-Encoding", "identity") != "identity":
                raise DataError("Unexpected HTTP content encoding")
            info.update(response_metadata(response))
            info["resolved_url"] = response.url
            shutil.copyfileobj(response, target, CHUNK)
        if info["content_length"] is not None and raw.stat().st_size != int(info["content_length"]):
            raise DataError(f"{name}: truncated HTTP download")
    info.update(file_info(raw))
    if "sha256" in metadata and metadata["sha256"] != info["sha256"]:
        raise DataError(f"{name}: expected input checksum does not match")
    decoded = stage / f"{name}.txt"
    with raw.open("rb") as stream:
        compressed = stream.read(2) == b"\x1f\x8b"
    opener = gzip.open if compressed else open
    with opener(raw, "rb") as source, decoded.open("wb") as target:
        shutil.copyfileobj(source, target, CHUNK)
    info["compression"] = "gzip" if compressed else "none"
    info["decoded"] = file_info(decoded)
    if "decoded_sha256" in metadata and metadata["decoded_sha256"] != info["decoded"]["sha256"]:
        raise DataError(f"{name}: expected decoded checksum does not match")
    return info
