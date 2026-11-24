"""Preflight writable destinations without changing files or acquiring locks."""

from pathlib import Path
import json
import re
import stat
import unicodedata

from .formats import DataError


CANDIDATE_NAME = re.compile(r"(?:snapshot|export|release|delivery)-v1-[a-f0-9]{64}")
CANDIDATE_MARKERS = ("snapshot.json", "manifest.json", "release.json", "index.json")


def historical_candidate(directory):
    marker = directory / 'latest.json'
    if not (marker.exists() or marker.is_symlink()):
        return False
    if any(parent.name.casefold() == 'comet-candidates' and parent.parent.name.casefold() == '.context'
           for parent in directory.parents):
        return True
    # Retained copies and redirected namespaces lose the lexical namespace.
    # Schema 3 is immutable; schema-2 and asteroid site pointers stay mutable.
    try:
        info = marker.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_size > 4096:
            return False
        value = json.loads(marker.read_bytes())
        return isinstance(value, dict) and value.get('comet_contract_version') == 3
    except (OSError, ValueError):
        return False


def validate_flat_inventory(directory, names, *, label):
    """Check the candidate's own entries before opening any of its contents."""
    if not stat.S_ISDIR(directory.lstat().st_mode):
        raise DataError(f"{label} must be a real directory")
    entries = {path.name: path for path in directory.iterdir()}
    if entries.keys() != set(names):
        missing = ', '.join(sorted(set(names) - entries.keys())) or 'none'
        unexpected = ', '.join(sorted(entries.keys() - set(names))) or 'none'
        raise DataError(f"{label} file inventory mismatch (missing: {missing}; unexpected: {unexpected})")
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
    """Reject named candidate ancestors and marker-bearing target directories."""
    resolved = resolved_path(path)
    for root in protected_roots:
        if path_within(resolved, root):
            raise DataError(f"{label} must be outside immutable inputs: {root}")
    for ancestor in (resolved, *resolved.parents):
        # Unlike mutable site products, historical candidates live in this
        # reserved local namespace. Protect their entire installed tree, even
        # through aliases, without blocking the external lock/staging area.
        if historical_candidate(ancestor):
            raise DataError(f'{label} must be outside immutable historical candidates: {ancestor}')
        if CANDIDATE_NAME.fullmatch(ancestor.name.casefold()) or (ancestor == resolved and ancestor.is_dir() and any(
                (ancestor / marker).exists() or (ancestor / marker).is_symlink()
                for marker in CANDIDATE_MARKERS)):
            raise DataError(f"{label} must not be at or inside an existing immutable snapshot, export, release or delivery candidate: {ancestor}")
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


def managed_tree(directory, pattern, *, children=(), reserved=(), label='Output'):
    """List one product's regular files without entering other owned namespaces.

    Reserved directories must be real directories, but their payload validation
    belongs to their own product verifier and the complete-site boundary.
    """
    directory = Path(directory)
    if not directory.exists() and not directory.is_symlink():
        return set()
    if not stat.S_ISDIR(directory.lstat().st_mode):
        raise DataError(f'{label} must be a real directory')
    names = set()
    for path in directory.iterdir():
        real_directory = stat.S_ISDIR(path.lstat().st_mode)
        if path.name in reserved and real_directory:
            continue
        entries = list(path.iterdir()) if path.name in children and real_directory else [path]
        for item in entries:
            name = item.relative_to(directory).as_posix()
            if not stat.S_ISREG(item.lstat().st_mode) or not pattern.fullmatch(name):
                raise DataError(f'Unexpected {label.lower()} entry: {name}')
            names.add(name)
    return names


def validate_same_filesystem(staging_parent, destinations, *, label):
    """Preflight atomic renames, using existing ancestors for future paths."""
    def device(path):
        path = Path(path)
        while True:
            try:
                return path.stat().st_dev
            except FileNotFoundError:
                if path.parent == path:
                    raise
                path = path.parent

    expected = device(staging_parent)
    for destination in destinations:
        if device(destination) != expected:
            raise DataError(f'{label} and staging parent must be on the same filesystem: {destination}')
