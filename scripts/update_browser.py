#!/usr/bin/env python3
"""Manual data update; generates files only, leaving Git review/publication to owners."""

import argparse
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from orrery_data.browser import managed_files
from orrery_data.paths import validate_writable_path, validate_disjoint_paths


def command(*args):
    result = subprocess.run([sys.executable, '-m', 'orrery_data', *map(str, args)],
                            cwd=ROOT, capture_output=True, text=True)
    if result.returncode:
        raise ValueError(result.stderr.strip())
    return json.loads(result.stdout)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--from-bundle', type=Path, help='reuse a complete verified indexed bundle without acquiring new data')
    parser.add_argument('--output', type=Path, default=ROOT / 'data')
    parser.add_argument('--mpcorb', type=Path)
    parser.add_argument('--numbered', type=Path)
    parser.add_argument('--source-metadata', type=Path)
    parser.add_argument('--allow-count-decrease', action='store_true')
    args = parser.parse_args()
    if args.from_bundle and (args.mpcorb or args.numbered or args.source_metadata or args.allow_count_decrease):
        parser.error('--from-bundle cannot be combined with acquisition options')
    if bool(args.mpcorb) != bool(args.numbered):
        parser.error('provide both --mpcorb and --numbered')
    # Resolve invocation-relative file arguments before running repository CLI.
    for key in ('from_bundle', 'output', 'mpcorb', 'numbered', 'source_metadata'):
        value = getattr(args, key)
        if value is not None:
            setattr(args, key, value.resolve())
    try:
        validate_writable_path(args.output, label='Browser output')
        validate_disjoint_paths(args.output, (ROOT / '.data', ROOT / 'artifacts'), label='Browser output')
        managed_files(args.output)
        bundle = args.from_bundle
        if bundle is None:
            refresh_args = ['--reuse-unchanged']
            if args.mpcorb:
                refresh_args += ['--mpcorb', args.mpcorb, '--numbered', args.numbered]
            if args.source_metadata:
                refresh_args += ['--source-metadata', args.source_metadata]
            if args.allow_count_decrease:
                refresh_args += ['--allow-count-decrease']
            snapshot = command('refresh', *refresh_args)
            exported = command('export', '--snapshot', snapshot['snapshot_version'])
            indexed = command('export-indexed', '--export', exported['path'])
            bundle = ROOT / indexed['path']
        result = command('export-browser', '--bundle', bundle, '--output', args.output)
        print(json.dumps(result, indent=2))
        return 0
    except (OSError, ValueError) as exc:
        print(json.dumps({'error': str(exc)}), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
