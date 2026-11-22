#!/usr/bin/env python3
"""Rebuild comet v2 midpoint evidence from the exact pinned raw inputs, offline."""

import argparse
import gzip
import hashlib
import io
import json
import math
from pathlib import Path
import stat
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from orrery_data import comets_v2 as comets
from orrery_data.formats import DataError
from orrery_data.horizons import MAX_RESPONSE, parse_response
from orrery_data.paths import validate_append_path, validate_disjoint_paths
from orrery_data.storage import atomic_bytes, encode, read_json, writer_lock


def pinned_rows(cache, source, object_id, midpoint, targets):
    path = cache / (source['sha256'] + '.raw')
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_RESPONSE:
        raise DataError('Invalid pinned Horizons raw input')
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != source['sha256']:
        raise DataError('Corrupt pinned Horizons raw input')
    rows, metadata = parse_response(raw, object_id, midpoint, targets=targets)
    if any(source[key] != value for key, value in metadata.items()):
        raise DataError('Pinned Horizons provenance differs from published product')
    return rows


def rebuild(directory, cache, output):
    directory, cache, output = Path(directory), Path(cache), Path(output)
    # Never let fixture output replace public products or retained scientific inputs.
    validate_append_path(output, label='Comet evidence output')
    validate_disjoint_paths(output, (directory.parent, cache), label='Comet evidence output')
    directory, cache, output = directory.resolve(), cache.resolve(), output.resolve()
    lock = directory.parent.parent / ('.' + directory.parent.name + '-browser-lock')
    with writer_lock(lock):
        verified = comets.verify_comets(directory)
        index = read_json(directory / verified['pin']['url'])
        targets = {entry['id']: entry['name'] for entry in index['objects']}
        evidence = {}
        for entry in index['objects']:
            object_id = entry['id']
            rows = pinned_rows(cache, entry['provenance']['vectors'], object_id, False, targets)
            middle = pinned_rows(cache, entry['provenance']['midpoints'], object_id, True, targets)
            series = read_json(directory / entry['data']['url'])['series']
            serialized = [[float(f'{value:.12g}') for value in row[1:]] for row in rows]
            if serialized != series['states']:
                raise DataError(f'{object_id}: pinned vectors differ from serialized states')
            errors = [math.dist(comets.interpolate(series, row[0]), row[1:4]) for row in middle]
            worst = max(range(len(errors)), key=errors.__getitem__)
            validation = entry['validation']
            if (max(errors) != validation['max_error_au'] or middle[worst][0] != validation['worst_jd']
                    or len(errors) != validation['samples'] or max(errors) > comets.TOLERANCE):
                raise DataError(f'{object_id}: numerical evidence differs from published validation')
            independent = {row[0]: row[:4] for row in middle}
            if any(independent.get(row[0]) != row for row in validation['references']):
                raise DataError(f'{object_id}: published references differ from pinned midpoints')
            evidence[object_id + '-midpoints'] = {
                'source': entry['provenance']['midpoints'], 'rows': [row[:4] for row in middle]}
        # GzipFile gives a portable header (no filename/clock/OS variation).
        compressed = io.BytesIO()
        with gzip.GzipFile(filename='', fileobj=compressed, mode='wb', mtime=0) as stream:
            stream.write(encode(evidence).encode('utf-8'))
        atomic_bytes(output, compressed.getvalue())
    return {'output': str(output), 'objects': len(evidence), 'bytes': output.stat().st_size}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', type=Path, default=ROOT / 'data/comets-v2')
    parser.add_argument('--cache', type=Path, default=ROOT / '.data/horizons')
    parser.add_argument('--output', type=Path, default=ROOT / 'tests/fixtures/comet-v2-midpoints.json.gz')
    args = parser.parse_args()
    try:
        print(json.dumps(rebuild(args.directory, args.cache, args.output)))
    except (OSError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
