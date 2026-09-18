#!/usr/bin/env python3
"""Validate a scheduled browser refresh and prepare its draft-PR metadata."""

import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
SUMMARY_FIELDS = ("pin", "records", "chunks", "files", "bytes")
COUNT_GUARDS = ("orbital_records", "discovery_records", "master_records", "known_discovery")


def read_json(path, label):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError(f"Invalid {label}: {exc}") from exc


def git_paths(repository, *args):
    result = subprocess.run(("git", *args), cwd=repository, capture_output=True)
    if result.returncode:
        raise ValueError(result.stderr.decode(errors="replace").strip() or "Unable to inspect Git changes")
    return {value.decode() for value in result.stdout.split(b"\0") if value}


def worktree_changes(repository):
    tracked = git_paths(repository, "diff", "--name-only", "--no-renames", "-z", "HEAD", "--")
    added = git_paths(repository, "diff", "--name-only", "--no-renames", "--diff-filter=A", "-z", "HEAD", "--")
    deleted = git_paths(repository, "diff", "--name-only", "--no-renames", "--diff-filter=D", "-z", "HEAD", "--")
    untracked = git_paths(repository, "ls-files", "--others", "--exclude-standard", "-z")
    added |= untracked
    paths = tracked | untracked
    unexpected = sorted(path for path in paths if not Path(path).parts or Path(path).parts[0] != "data")
    if unexpected:
        raise ValueError("Scheduled refresh changed files outside data/: " + ", ".join(unexpected))
    strip_data = lambda values: {path.removeprefix("data/") for path in values}
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
    index = read_json(index_path, "browser index")
    for field in ("snapshot_version", "catalog_id", "counts", "sources"):
        if field not in index:
            raise ValueError(f"Browser index is missing {field}")
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


def run_identity():
    run_id, attempt = os.environ.get("GITHUB_RUN_ID", ""), os.environ.get("GITHUB_RUN_ATTEMPT", "")
    if not run_id.isdigit() or not attempt.isdigit():
        raise ValueError("GITHUB_RUN_ID and GITHUB_RUN_ATTEMPT must be positive integers")
    return run_id, attempt


def pull_request_body(index, refresh, run_url):
    counts, sources, changes = index["counts"], index["sources"], refresh["changes"]
    lines = [
        "Automated weekly refresh through the existing MPC producer pipeline.",
        "",
        "- Browser records: {:,}".format(refresh["records"]),
        f"- Chunks/files: {refresh['chunks']} / {refresh['files']}",
        f"- Snapshot: `{index['snapshot_version']}`",
        f"- Index: `{refresh['pin']['sha256']}`",
        "- Orbital / known-discovery / missing-discovery records: "
        "{:,} / {:,} / {:,}".format(
            counts["orbital_records"], counts["known_discovery"], counts["missing_discovery"]),
        f"- MPCORB last modified: {sources['mpcorb']['last_modified']}",
        f"- NumberedMPs last modified: {sources['numbered']['last_modified']}",
        "- Public-file churn: {} added, {} changed, {} deleted, {} unchanged".format(
            len(changes["added"]), len(changes["changed"]),
            len(changes["deleted"]), len(changes["unchanged"])),
    ]
    if run_url:
        lines.append(f"- Workflow run: {run_url}")
    lines += [
        "",
        "The updater ran without `--allow-count-decrease`; committed counts were rechecked, and `verify-browser` passed before this PR was created.",
        "This PR is deliberately draft and is never auto-merged. Merging it triggers the existing committed-data Pages publication workflow.",
    ]
    return "\n".join(lines) + "\n"


def append_lines(path, lines):
    with path.open("a", encoding="utf-8") as target:
        target.write("".join(f"{key}={value}\n" for key, value in lines.items()))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", type=Path, default=ROOT)
    parser.add_argument("--refresh-result", type=Path, required=True)
    parser.add_argument("--verification-result", type=Path, required=True)
    parser.add_argument("--body", type=Path, required=True)
    parser.add_argument("--github-output", type=Path)
    parser.add_argument("--github-summary", type=Path)
    args = parser.parse_args()
    try:
        repository = args.repository.resolve()
        refresh = read_json(args.refresh_result, "refresh result")
        verification = read_json(args.verification_result, "verification result")
        git_changes = worktree_changes(repository)
        validate_results(repository, refresh, verification, git_changes)
        index = load_index(repository, refresh["pin"])
        reject_count_decreases(committed_index(repository), index)
        paths = git_changes["paths"]
        run_id, attempt = run_identity()
        branch = f"automation/mpc-refresh-{run_id}-{attempt}"
        title = f"Refresh MPC browser data ({source_date(index)})"
        server = os.environ.get("GITHUB_SERVER_URL", "https://github.com").rstrip("/")
        slug = os.environ.get("GITHUB_REPOSITORY", "")
        run_url = f"{server}/{slug}/actions/runs/{run_id}" if slug else ""
        body = pull_request_body(index, refresh, run_url)
        args.body.parent.mkdir(parents=True, exist_ok=True)
        args.body.write_text(body, encoding="utf-8")
        outputs = {"changed": str(bool(paths)).lower(), "branch": branch,
                   "title": title, "body": str(args.body.resolve())}
        if args.github_output:
            append_lines(args.github_output, outputs)
        summary = (f"## MPC browser refresh\n\n"
                   f"- Result: {'review required' if paths else 'no committed data change'}\n"
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
