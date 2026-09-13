"""Preflight writable destinations without changing files or acquiring locks."""

from pathlib import Path
import re

from .formats import DataError


CANDIDATE_NAME = re.compile(r"(?:snapshot|export|release)-v1-[a-f0-9]{64}")
CANDIDATE_MARKERS = ("snapshot.json", "manifest.json", "release.json")


def resolved_path(path):
    try:
        return Path(path).resolve()
    except RuntimeError as exc:
        raise DataError(f"Cannot resolve writable path: {path}: {exc}") from exc


def validate_writable_path(path, *, label="Output", protected_roots=()):
    """Reject immutable ancestors, including renamed copies and path aliases."""
    resolved = resolved_path(path)
    for root in protected_roots:
        if resolved.is_relative_to(resolved_path(root)):
            raise DataError(f"{label} must be outside immutable inputs: {root}")
    for ancestor in (resolved, *resolved.parents):
        if CANDIDATE_NAME.fullmatch(ancestor.name) or (ancestor.is_dir() and any(
                (ancestor / marker).exists() or (ancestor / marker).is_symlink()
                for marker in CANDIDATE_MARKERS)):
            raise DataError(f"{label} must not be at or inside an existing immutable snapshot, export or release candidate: {ancestor}")
    return resolved


def validate_disjoint_paths(writable, inputs, *, label):
    """Require dedicated writer/input roots; neither may contain the other."""
    resolved = resolved_path(writable)
    for source in inputs:
        source = resolved_path(source)
        if resolved.is_relative_to(source) or source.is_relative_to(resolved):
            raise DataError(f"{label} must not overlap retained inputs or producer code: {source}")
    return resolved
