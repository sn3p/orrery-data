#!/usr/bin/env python3
"""Manual data update; generates files only, leaving Git review/publication to owners."""

import argparse
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from orrery_data.browser import managed_files, export_browser
from orrery_data.storage import read_json, file_info
from orrery_data.named_bodies import validate_definitions
from scripts.named_body_candidate import build as build_named_candidate
from orrery_data.paths import validate_writable_path, validate_disjoint_paths


def command(*args):
    result = subprocess.run([sys.executable, '-m', 'orrery_data', *map(str, args)],
                            cwd=ROOT, capture_output=True, text=True)
    if result.returncode:
        raise ValueError(result.stderr.strip())
    return json.loads(result.stdout)


def update_from_bundle(bundle, output):
    """Refresh the exact inactive companion before installing the asteroid pin."""
    definitions = ROOT / 'research/named-bodies/definitions.json'
    validate_definitions(read_json(definitions))
    companion = None
    def prepare(bundle, stage):
        nonlocal companion
        pin = read_json(stage / 'latest.json')['index']['sha256']
        science = file_info(definitions)['sha256']
        destination = ROOT / '.context/named-body-candidates' / f'refresh-{pin}-{science}'
        companion = build_named_candidate(bundle / 'full', stage, destination, definitions)
    result = export_browser(bundle, output, before_install=prepare)
    return {**result, 'named_bodies': {**companion, 'activation': 'inactive'}}


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
        validate_definitions(read_json(ROOT / 'research/named-bodies/definitions.json'))
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
        result = update_from_bundle(bundle, args.output)
        print(json.dumps(result, indent=2))
        return 0
    except (OSError, ValueError) as exc:
        print(json.dumps({'error': str(exc)}), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
