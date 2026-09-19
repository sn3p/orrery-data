#!/usr/bin/env python3
"""Validate a scheduled browser refresh and prepare its commit metadata."""

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from orrery_data.paths import path_within, paths_overlap, resolved_path, validate_append_path


SUMMARY_FIELDS = ("pin", "records", "chunks", "files", "bytes")
COUNT_GUARDS = ("orbital_records", "discovery_records", "master_records", "known_discovery")
REQUIRED_COUNTS = (*COUNT_GUARDS, "missing_discovery")
NONNEGATIVE_SUMMARY_FIELDS = ("records", "chunks", "files", "bytes")


def read_json(path, label):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError(f"Invalid {label}: {exc}") from exc


def validate_result_shape(result, label):
    if not isinstance(result, dict):
        raise ValueError(f"{label.title()} result must be an object")
    if not isinstance(result.get("pin"), dict) or set(result["pin"]) != {"url", "sha256", "bytes"}:
        raise ValueError(f"{label.title()} result has an invalid pin")
    pin = result["pin"]
    digest = pin["sha256"]
    if (not isinstance(digest, str) or not re.fullmatch(r"[a-f0-9]{64}", digest)
            or pin["url"] != f"index-{digest}.json"
            or type(pin["bytes"]) is not int or pin["bytes"] < 0):
        raise ValueError(f"{label.title()} result has an invalid pin")
    for field in NONNEGATIVE_SUMMARY_FIELDS:
        if field not in result or type(result[field]) is not int or result[field] < 0:
            raise ValueError(f"{label.title()} result has an invalid {field}")
    if not isinstance(result.get("path"), str) or not result["path"]:
        raise ValueError(f"{label.title()} result has an invalid path")


def validate_append_targets(repository, protected, **targets):
    repository = resolved_path(repository)
    protected = [resolved_path(path) for path in protected]
    resolved_targets = {}
    for label, path in targets.items():
        if path is None:
            continue
        resolved = resolved_path(path)
        if path_within(resolved, repository):
            raise ValueError(f"{label} must be outside the repository worktree")
        if any(paths_overlap(resolved, candidate)
               for candidate in (*protected, *resolved_targets.values())):
            raise ValueError(f"{label} overlaps another helper path")
        resolved = validate_append_path(path, label=label)
        if not resolved.parent.is_dir():
            raise ValueError(f"{label} parent must already exist and be a directory")
        resolved_targets[label] = resolved


def git_paths(repository, *args):
    result = subprocess.run(("git", *args), cwd=repository, capture_output=True)
    if result.returncode:
        raise ValueError(result.stderr.decode(errors="replace").strip() or "Unable to inspect Git changes")
    return {os.fsdecode(value) for value in result.stdout.split(b"\0") if value}


def worktree_changes(repository):
    tracked = git_paths(repository, "diff", "--name-only", "--no-renames", "-z", "HEAD", "--")
    added = git_paths(repository, "diff", "--name-only", "--no-renames", "--diff-filter=A", "-z", "HEAD", "--")
    deleted = git_paths(repository, "diff", "--name-only", "--no-renames", "--diff-filter=D", "-z", "HEAD", "--")
    untracked = git_paths(repository, "ls-files", "--others", "--exclude-standard", "-z")
    added |= untracked
    paths = tracked | untracked
    parsed_paths = {path: PurePosixPath(path) for path in paths}
    unexpected = sorted(
        path for path, parsed in parsed_paths.items()
        if len(parsed.parts) < 2 or parsed.parts[0] != "data"
    )
    if unexpected:
        raise ValueError("Scheduled refresh changed files outside data/: " + ", ".join(unexpected))
    def strip_data(values):
        return {parsed_paths[path].relative_to("data").as_posix() for path in values}

    return {"added": strip_data(added), "changed": strip_data(tracked - added - deleted),
            "deleted": strip_data(deleted), "paths": paths}


def inventory_set(values, label):
    result = set()
    for value in values:
        if (not isinstance(value, str) or not value or Path(value).is_absolute()
                or value != Path(value).as_posix() or ".." in Path(value).parts):
            raise ValueError(f"Refresh {label} inventory has an invalid path")
        if value in result:
            raise ValueError(f"Refresh {label} inventory contains a duplicate")
        result.add(value)
    return result


def validate_results(repository, refresh, verification, git_changes):
    validate_result_shape(refresh, "refresh")
    validate_result_shape(verification, "verification")
    if refresh.get("status") not in {"updated", "unchanged"}:
        raise ValueError("Refresh result has an invalid status")
    for field in SUMMARY_FIELDS:
        if refresh.get(field) != verification.get(field):
            raise ValueError(f"Refresh and verification disagree on {field}")
    for label, result in (("refresh", refresh), ("verification", verification)):
        try:
            result_path = Path(result["path"])
        except (KeyError, TypeError) as exc:
            raise ValueError(f"{label.title()} result has no valid path") from exc
        if not result_path.is_absolute():
            result_path = repository / result_path
        if result_path.resolve() != (repository / "data").resolve():
            raise ValueError(f"{label.title()} result does not describe repository data/")
    changes = refresh.get("changes")
    if not isinstance(changes, dict) or set(changes) != {"added", "changed", "unchanged", "deleted"}:
        raise ValueError("Refresh result has no complete change inventory")
    if any(not isinstance(changes[key], list) for key in changes):
        raise ValueError("Refresh change inventory must contain lists")
    inventory = {key: inventory_set(changes[key], key) for key in changes}
    for index, left in enumerate(inventory):
        for right in tuple(inventory)[index + 1:]:
            if inventory[left] & inventory[right]:
                raise ValueError(f"Refresh inventories overlap: {left} and {right}")
    current = inventory["added"] | inventory["changed"] | inventory["unchanged"]
    if len(current) != refresh["files"]:
        raise ValueError("Refresh inventory does not match the verified file count")
    for key in ("added", "changed", "deleted"):
        if inventory[key] != git_changes[key]:
            raise ValueError(f"Refresh {key} inventory disagrees with Git")
    if (refresh["status"] == "updated") != bool(git_changes["paths"]):
        raise ValueError("Refresh status disagrees with Git changes")


def load_index(repository, pin):
    try:
        name = pin["url"]
        if pin["sha256"] not in name:
            raise ValueError("pin hash is not present in its URL")
    except (KeyError, TypeError) as exc:
        raise ValueError("Refresh pin is invalid") from exc
    data = (repository / "data").resolve()
    index_path = (data / name).resolve()
    if index_path.parent != data or not re.fullmatch(r"index-[a-f0-9]{64}\.json", index_path.name):
        raise ValueError("Refresh pin does not reference a root index")
    try:
        index_bytes = index_path.read_bytes()
    except OSError as exc:
        raise ValueError(f"Invalid browser index: {exc}") from exc
    if len(index_bytes) != pin["bytes"] or hashlib.sha256(index_bytes).hexdigest() != pin["sha256"]:
        raise ValueError("Refresh pin does not match the browser index")
    index = read_json(index_path, "browser index")
    for field in ("snapshot_version", "catalog_id", "counts", "sources"):
        if field not in index:
            raise ValueError(f"Browser index is missing {field}")
    counts = index["counts"]
    if not isinstance(counts, dict):
        raise ValueError("Browser index counts must be an object")
    for key in REQUIRED_COUNTS:
        if key not in counts:
            raise ValueError(f"Browser index is missing required count {key}")
        if type(counts[key]) is not int or counts[key] < 0:
            raise ValueError(f"Browser index has an invalid required count {key}")
    return index


def committed_index(repository):
    def read_committed(path, label):
        result = subprocess.run(("git", "show", f"HEAD:{path}"), cwd=repository,
                                capture_output=True, text=True)
        if result.returncode:
            raise ValueError(result.stderr.strip() or f"Unable to read committed {label}")
        try:
            return json.loads(result.stdout)
        except ValueError as exc:
            raise ValueError(f"Invalid committed {label}: {exc}") from exc

    pointer = read_committed("data/latest.json", "browser descriptor")
    try:
        name = pointer["index"]["url"]
    except (KeyError, TypeError) as exc:
        raise ValueError("Committed browser descriptor has no index URL") from exc
    if not isinstance(name, str) or not re.fullmatch(r"index-[a-f0-9]{64}\.json", name):
        raise ValueError("Committed browser descriptor has an invalid index URL")
    return read_committed(f"data/{name}", "browser index")


def reject_count_decreases(previous, current):
    for key in COUNT_GUARDS:
        try:
            before, after = previous["counts"][key], current["counts"][key]
        except (KeyError, TypeError) as exc:
            raise ValueError(f"Browser index is missing guarded count {key}") from exc
        if (not isinstance(before, int) or isinstance(before, bool)
                or not isinstance(after, int) or isinstance(after, bool)):
            raise ValueError(f"Browser index has an invalid guarded count {key}")
        if after < before:
            raise ValueError(f"{key} decreased from committed {before} to {after}; inspect sources manually")


def source_date(index):
    dates = []
    for name in ("mpcorb", "numbered"):
        try:
            value = index["sources"][name]["retrieved_at"]
        except (KeyError, TypeError) as exc:
            raise ValueError(f"Browser index has no {name} retrieval time") from exc
        if not isinstance(value, str) or not re.match(r"^\d{4}-\d{2}-\d{2}T", value):
            raise ValueError(f"Browser index has an invalid {name} retrieval time")
        dates.append(value[:10])
    return max(dates)


def append_lines(path, lines):
    with path.open("a", encoding="utf-8") as target:
        target.write("".join(f"{key}={value}\n" for key, value in lines.items()))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", type=Path, default=ROOT)
    parser.add_argument("--refresh-result", type=Path, required=True)
    parser.add_argument("--verification-result", type=Path, required=True)
    parser.add_argument("--github-output", type=Path)
    parser.add_argument("--github-summary", type=Path)
    args = parser.parse_args()
    try:
        repository = args.repository.resolve()
        validate_append_targets(
            repository,
            (args.refresh_result, args.verification_result),
            github_output=args.github_output,
            github_summary=args.github_summary,
        )
        refresh = read_json(args.refresh_result, "refresh result")
        verification = read_json(args.verification_result, "verification result")
        git_changes = worktree_changes(repository)
        validate_results(repository, refresh, verification, git_changes)
        index = load_index(repository, refresh["pin"])
        reject_count_decreases(committed_index(repository), index)
        paths = git_changes["paths"]
        title = f"Refresh MPC browser data ({source_date(index)})"
        outputs = {"changed": str(bool(paths)).lower(), "title": title}
        if args.github_output:
            append_lines(args.github_output, outputs)
        summary = (f"## MPC browser refresh\n\n"
                   f"- Result: {'commit required' if paths else 'no committed data change'}\n"
                   f"- Records: {refresh['records']:,}\n"
                   f"- Chunks: {refresh['chunks']}\n"
                   f"- Index: `{refresh['pin']['sha256']}`\n")
        if args.github_summary:
            with args.github_summary.open("a", encoding="utf-8") as target:
                target.write(summary)
        print(json.dumps({**outputs, "changed": bool(paths), "paths": len(paths),
                          "snapshot_version": index["snapshot_version"]}))
        return 0
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(json.dumps({"error": str(exc)}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
