"""JSON-on-stdout command boundaries; errors do not advance active versions."""

import argparse
from datetime import date
import http.client
from pathlib import Path
import re
import sqlite3
import sys
import zlib

from . import __version__
from .database import DEFAULT_DATABASE, build_database, database_info, query_database
from .formats import DataError, julian_day
from .metadata import validate_source_metadata
from .pipeline import check, export, refresh
from .storage import URLS, encode, read_json
from .releases import prepare_release, verify_release
from .indexed import DEFAULT_CHUNK_BYTES, export_indexed, verify_indexed


def positive(value):
    number = int(value)
    if number <= 0:
        raise argparse.ArgumentTypeError("must be positive")
    return number


def nonnegative(value):
    number = int(value)
    if number < 0:
        raise argparse.ArgumentTypeError("must be nonnegative")
    return number


def query_limit(value):
    number = positive(value)
    if number > 10000:
        raise argparse.ArgumentTypeError("must be at most 10000")
    return number


def discovery_date(value):
    try:
        if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
            raise ValueError()
        return julian_day(date.fromisoformat(value))
    except ValueError as exc:
        raise argparse.ArgumentTypeError("expected a valid YYYY-MM-DD date") from exc


def parser():
    root = argparse.ArgumentParser(description="Validated MPC snapshots and Orrery catalogs")
    root.add_argument("--version", action="version", version=__version__)
    commands = root.add_subparsers(dest="command", required=True)
    for name in ("check", "refresh", "export", "build-db", "prepare-release"):
        cmd = commands.add_parser(name)
        cmd.add_argument("--store", type=Path, default=Path(".data"), help="local snapshot store (default: .data)")
        if name in ("check", "refresh", "prepare-release"):
            cmd.add_argument("--mpcorb-url", default=URLS["mpcorb"])
            cmd.add_argument("--numbered-url", default=URLS["numbered"])
            cmd.add_argument("--timeout", type=positive, default=None if name == "prepare-release" else 60,
                             help="HTTP socket timeout in seconds (default: 60)")
        if name in ("refresh", "prepare-release"):
            cmd.add_argument("--mpcorb", type=Path, help="import local MPCORB (plain or gzip)")
            cmd.add_argument("--numbered", type=Path, help="import local NumberedMPs (plain or gzip)")
            cmd.add_argument("--source-metadata", type=Path, help="optional per-source JSON provenance and expected hashes")
            cmd.add_argument("--allow-count-decrease", action="store_true", help="accept an inspected reduction in source/eligible counts")
        if name in ("export", "build-db", "prepare-release"):
            cmd.add_argument("--snapshot", help=("pin a snapshot for offline preparation; otherwise refresh sources"
                                                if name == "prepare-release" else "pin a snapshot version (default: current)"))
        if name == "prepare-release":
            cmd.add_argument("--output", type=Path, default=Path("artifacts/releases"))
            cmd.add_argument("--producer-commit", required=True, help="full Git commit of the producing code")
            cmd.add_argument("--selected-limit", type=positive, action="append",
                             help="include first N eligible rows; repeat for multiple profiles (default: 100000)")
            cmd.add_argument("--baseline-counts", type=Path, help="JSON counts from a previously inspected dataset")
        if name == "build-db":
            cmd.add_argument("--database", type=Path, default=DEFAULT_DATABASE,
                             help="database to atomically replace (default: artifacts/orrery.sqlite3)")
        elif name == "export":
            cmd.add_argument("--output", type=Path, default=Path("artifacts"))
            cmd.add_argument("--limit", type=positive, help="select first N eligible MPCORB records before sorting; default: all")
    for name in ("db-info", "query"):
        cmd = commands.add_parser(name)
        cmd.add_argument("--database", type=Path, default=DEFAULT_DATABASE,
                         help="existing local database (default: artifacts/orrery.sqlite3)")
        if name == "db-info":
            cmd.add_argument("--verify", action="store_true", help="also run full integrity and foreign key checks")
        else:
            exact = cmd.add_mutually_exclusive_group()
            exact.add_argument("--id", dest="object_id", help="exact MPC authority ID")
            exact.add_argument("--number", type=positive, help="exact permanent MPC number")
            exact.add_argument("--packed-designation", help="exact, case-sensitive packed MPC designation")
            cmd.add_argument("--discovery", choices=("all", "known", "missing"), default="all")
            cmd.add_argument("--discovered-from", type=discovery_date, help="inclusive YYYY-MM-DD calendar date")
            cmd.add_argument("--discovered-to", type=discovery_date, help="inclusive YYYY-MM-DD calendar date")
            cmd.add_argument("--order", choices=("source", "discovery"), default="source",
                             help="source order, or discovery date with nulls last and source-order ties")
            cmd.add_argument("--limit", type=query_limit, default=20, help="page size 1–10000 (default: 20)")
            cmd.add_argument("--offset", type=nonnegative, default=0, help="skip matching rows (default: 0)")
    verify = commands.add_parser("verify-release")
    verify.add_argument("--bundle", type=Path, required=True)
    verify.add_argument("--manifest-sha256", help="expected release.json hash from a trusted channel")
    indexed = commands.add_parser("export-indexed", help="optional offline derivative of an existing full/limited export")
    indexed.add_argument("--export", dest="source_export", type=Path, required=True)
    indexed.add_argument("--output", type=Path, default=Path("artifacts/indexed"))
    indexed.add_argument("--chunk-bytes", type=positive, default=DEFAULT_CHUNK_BYTES,
                         help="maximum decoded bytes per file (default: 1 MiB; maximum: 8 MiB)")
    indexed_verify = commands.add_parser("verify-indexed")
    indexed_verify.add_argument("--bundle", type=Path, required=True)
    indexed_verify.add_argument("--index-sha256", help="expected index.json hash from a trusted channel")
    return root


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        if args.command == "check":
            result = check(args.store, {name: getattr(args, name + "_url") for name in URLS}, args.timeout)
        elif args.command == "refresh":
            if bool(args.mpcorb) != bool(args.numbered):
                raise DataError("Provide both --mpcorb and --numbered, or neither")
            metadata = read_json(args.source_metadata) if args.source_metadata else {}
            validate_source_metadata(metadata)
            result = refresh(args.store, {name: getattr(args, name + "_url") for name in URLS},
                             {name: getattr(args, name) for name in URLS}, metadata, args.timeout, args.allow_count_decrease)
        elif args.command == "prepare-release":
            if bool(args.mpcorb) != bool(args.numbered):
                raise DataError("Provide both --mpcorb and --numbered, or neither")
            if args.snapshot is not None and (
                    args.mpcorb or args.source_metadata or args.timeout is not None
                    or args.mpcorb_url != URLS["mpcorb"] or args.numbered_url != URLS["numbered"]):
                raise DataError("--snapshot cannot be combined with source acquisition options")
            metadata = read_json(args.source_metadata) if args.source_metadata else {}
            validate_source_metadata(metadata)
            baseline = read_json(args.baseline_counts) if args.baseline_counts else None
            result = prepare_release(args.store, args.output, args.producer_commit, version=args.snapshot,
                                     limits=args.selected_limit, urls={name: getattr(args, name + "_url") for name in URLS},
                                     local={name: getattr(args, name) for name in URLS}, metadata=metadata,
                                     timeout=args.timeout, allow_count_decrease=args.allow_count_decrease,
                                     baseline_counts=baseline)
        elif args.command == "verify-release":
            result = verify_release(args.bundle, args.manifest_sha256)
        elif args.command == "export-indexed":
            result = export_indexed(args.source_export, args.output, args.chunk_bytes)
        elif args.command == "verify-indexed":
            result = verify_indexed(args.bundle, args.index_sha256)
        elif args.command == "export":
            result = export(args.store, args.output, args.snapshot, args.limit)
        elif args.command == "build-db":
            result = build_database(args.store, args.database, args.snapshot)
        elif args.command == "db-info":
            result = database_info(args.database, args.verify)
        else:
            result = query_database(args.database, **{k: getattr(args, k) for k in (
                "object_id", "number", "packed_designation", "discovery", "discovered_from", "discovered_to",
                "order", "limit", "offset")})
    except (OSError, ValueError, KeyError, TypeError, EOFError, OverflowError, sqlite3.Error,
            zlib.error, http.client.HTTPException) as exc:
        print(encode({"error": str(exc)}), file=sys.stderr)
        return 1
    print(encode(result))
    return 2 if args.command == "check" and any(r["status"] == "unknown" for r in result["sources"].values()) else 0
