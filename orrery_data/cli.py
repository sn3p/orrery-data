"""JSON-on-stdout command boundaries; errors do not advance active versions."""

import argparse
import http.client
from pathlib import Path
import sys
import zlib

from . import __version__
from .formats import DataError
from .pipeline import check, export, refresh
from .storage import URLS, encode, read_json


def positive(value):
    number = int(value)
    if number <= 0:
        raise argparse.ArgumentTypeError("must be positive")
    return number


def parser():
    root = argparse.ArgumentParser(description="Validated MPC snapshots and Orrery catalogs")
    root.add_argument("--version", action="version", version=__version__)
    commands = root.add_subparsers(dest="command", required=True)
    for name in ("check", "refresh", "export"):
        cmd = commands.add_parser(name)
        cmd.add_argument("--store", type=Path, default=Path(".data"), help="local snapshot store (default: .data)")
        if name in ("check", "refresh"):
            cmd.add_argument("--mpcorb-url", default=URLS["mpcorb"])
            cmd.add_argument("--numbered-url", default=URLS["numbered"])
            cmd.add_argument("--timeout", type=positive, default=60, help="HTTP socket timeout in seconds")
        if name == "refresh":
            cmd.add_argument("--mpcorb", type=Path, help="import local MPCORB (plain or gzip)")
            cmd.add_argument("--numbered", type=Path, help="import local NumberedMPs (plain or gzip)")
            cmd.add_argument("--source-metadata", type=Path, help="optional per-source JSON provenance and expected hashes")
            cmd.add_argument("--allow-count-decrease", action="store_true", help="accept an inspected reduction in source/eligible counts")
        if name == "export":
            cmd.add_argument("--output", type=Path, default=Path("artifacts"))
            cmd.add_argument("--snapshot", help="pin a snapshot version (default: current)")
            cmd.add_argument("--limit", type=positive, help="select first N eligible MPCORB records before sorting; default: all")
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
            if not isinstance(metadata, dict) or not all(k in URLS and isinstance(v, dict) for k, v in metadata.items()):
                raise DataError("Source metadata must contain mpcorb/numbered objects")
            allowed_fields = {"retrieved_at", "last_modified", "etag", "content_length", "sha256", "decoded_sha256"}
            for name, fields in metadata.items():
                unknown = fields.keys() - allowed_fields
                if unknown:
                    raise DataError(f"{name}: unknown source metadata fields: {', '.join(sorted(unknown))}")
            result = refresh(args.store, {name: getattr(args, name + "_url") for name in URLS},
                             {name: getattr(args, name) for name in URLS}, metadata, args.timeout, args.allow_count_decrease)
        else:
            result = export(args.store, args.output, args.snapshot, args.limit)
    except (OSError, ValueError, KeyError, TypeError, EOFError, zlib.error, http.client.HTTPException) as exc:
        print(encode({"error": str(exc)}), file=sys.stderr)
        return 1
    print(encode(result))
    return 2 if args.command == "check" and any(r["status"] == "unknown" for r in result["sources"].values()) else 0
