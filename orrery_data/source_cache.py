"""Optional HTTP cache; immutable snapshots remain the source of truth."""

import shutil
import tempfile
import zlib
from pathlib import Path

from .contracts import SOURCE
from .formats import DataError
from .metadata import validate_source_metadata
from .storage import atomic_json, read_json, verify_file, verify_decoded_source


def cached_source(directory, name, url):
    path, metadata = directory / f"{name}.input", directory / f"{name}.json"
    try:
        if path.is_symlink() or metadata.is_symlink():
            return None
        info = read_json(metadata)
        SOURCE(info, "cached source")
        # Check the full entity-tag grammar before constructing a request header.
        validate_source_metadata({name: {"etag": info["etag"]}})
        if (info["url"] != url or info["acquisition"] != "http" or not info["retrieved_at"]
                or not info.get("resolved_url") or not info["etag"]
                or info["etag"].startswith('W/')):
            return None
        if info["content_length"] is not None and int(info["content_length"]) != info["bytes"]:
            return None
        verify_file(path, info)
        verify_decoded_source(path, info)
        return path, info
    except (OSError, ValueError, KeyError, TypeError, EOFError, zlib.error):
        # An absent/damaged cache cannot justify a conditional request.
        return None


def save_sources(directory, snapshot, sources):
    if directory.is_symlink():
        raise DataError("HTTP cache must be a real directory")
    directory.mkdir(exist_ok=True)
    for name, info in sources.items():
        if info["acquisition"] != "http":
            continue
        if cached_source(directory, name, info["url"]) == (directory / f"{name}.input", info):
            continue
        with tempfile.TemporaryDirectory(prefix=".cache-", dir=directory) as temp:
            copied = Path(temp) / "input"
            shutil.copyfile(snapshot / f"{name}.input", copied)
            copied.replace(directory / f"{name}.input")
            atomic_json(directory / f"{name}.json", info)
