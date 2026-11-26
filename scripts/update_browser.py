#!/usr/bin/env python3
"""Manual data update; generates files only, leaving Git review/publication to owners."""

import argparse
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from orrery_data.paired import update as update_paired, BASE, validate_base
from orrery_data.browser import managed_files
from orrery_data.storage import read_json, file_info
from orrery_data.named_bodies import validate_definitions
from scripts.named_body_candidate import build as build_named_candidate
from orrery_data.paths import validate_writable_path, validate_disjoint_paths


def pages_base(value):
    """Pages can report HTTP when enforcement is off; consumers require HTTPS.

    An unconfigured Pages host defers pairing, not asteroid acquisition. A host
    without working HTTPS cannot confirm supersession or expire retained pins.
    """
    if not value:
        return None
    if value.startswith('http://'):
        value = 'https://' + value[len('http://'):]
    return validate_base(value)


def command(*args):
    result = subprocess.run([sys.executable, '-m', 'orrery_data', *map(str, args)],
                            cwd=ROOT, capture_output=True, text=True)
    if result.returncode:
        raise ValueError(result.stderr.strip())
    return json.loads(result.stdout)


def update_from_bundle(bundle, output, *, base_url=BASE):
    """Publish a verified pair and bounded retained generations atomically."""
    definitions = ROOT / 'research/named-bodies/definitions.json'
    validate_definitions(read_json(definitions))
    def prepare(bundle, stage):
        pin = read_json(stage / 'latest.json')['index']['sha256']
        science = file_info(definitions)['sha256']
        destination = ROOT / '.context/named-body-candidates' / f'refresh-{pin}-{science}'
        return build_named_candidate(bundle / 'full', stage, destination, definitions)
    return update_paired(bundle, output, prepare, base_url=base_url)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    base = parser.add_mutually_exclusive_group()
    base.add_argument('--base-url', default=BASE, help='absolute HTTPS base for pair references and publication confirmation')
    base.add_argument('--pages-base-url', help='configure-pages base; normalize HTTP to HTTPS, or defer pairing if empty')
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
        base_url = pages_base(args.pages_base_url) if args.pages_base_url is not None else validate_base(args.base_url)
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
        result = update_from_bundle(bundle, args.output, base_url=base_url)
        print(json.dumps(result, indent=2))
        return 0
    except (OSError, ValueError) as exc:
        print(json.dumps({'error': str(exc)}), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
