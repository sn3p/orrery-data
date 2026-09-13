"""Preflight writable destinations without changing files or acquiring locks."""

from pathlib import Path
import re
import stat
import unicodedata

from .formats import DataError


CANDIDATE_NAME = re.compile(r"(?:snapshot|export|release)-v1-[a-f0-9]{64}")
CANDIDATE_MARKERS = ("snapshot.json", "manifest.json", "release.json")


def validate_flat_inventory(directory, names, *, label):
    """Check the candidate's own entries before opening any of its contents."""
    if not stat.S_ISDIR(directory.lstat().st_mode):
        raise DataError(f"{label} must be a real directory")
    entries = {path.name: path for path in directory.iterdir()}
    if entries.keys() != set(names):
        raise DataError(f"{label} file inventory mismatch")
    for path in entries.values():
        if not stat.S_ISREG(path.lstat().st_mode):
            raise DataError(f"{label} must contain only regular non-symlink files: {path.name}")


def resolved_path(path):
    try:
        return Path(path).resolve()
    except RuntimeError as exc:
        raise DataError(f"Cannot resolve writable path: {path}: {exc}") from exc


def path_within(path, root):
    """Include portable spellings and existing filesystem aliases of a root."""
    path, root = resolved_path(path), resolved_path(root)
    # Case/Unicode-only differences can alias on a supported filesystem even
    # when they are distinct on this runner. Reserve those names everywhere.
    names = [Path(unicodedata.normalize("NFC", str(value).casefold())) for value in (path, root)]
    if names[0].is_relative_to(names[1]):
        return True
    # resolve() follows symlinks but can preserve other filesystem aliases.
    if root.exists():
        for ancestor in (path, *path.parents):
            if ancestor.exists() and ancestor.samefile(root):
                return True
    return False


def paths_overlap(first, second):
    """Reserve one portable namespace for existing and future path aliases."""
    return path_within(first, second) or path_within(second, first)


def validate_writable_path(path, *, label="Output", protected_roots=()):
    """Reject immutable ancestors, including renamed copies and path aliases."""
    resolved = resolved_path(path)
    for root in protected_roots:
        if path_within(resolved, root):
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
        if paths_overlap(resolved, source):
            raise DataError(f"{label} must not overlap retained inputs or producer code: {source}")
    return resolved


def validate_append_path(path, *, label="Append destination"):
    """An append must own one regular inode; replacing a link is not possible."""
    path = Path(path)
    resolved = validate_writable_path(path, label=label)
    try:
        info = path.lstat()
    except FileNotFoundError:
        return resolved
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise DataError(f"{label} must be a regular file with exactly one link")
    return resolved
