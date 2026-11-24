#!/usr/bin/env python3
"""Rebuild comet midpoint evidence from the exact pinned raw inputs, offline."""

import argparse
import gzip
import io
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from orrery_data.comet_rollout import verify_current_comets
from orrery_data.comet_product import serialize_states, measure_interpolation
from orrery_data.formats import DataError
from orrery_data.horizons import parse_response, read_raw
from orrery_data.paths import validate_append_path, validate_disjoint_paths
from orrery_data.storage import atomic_bytes, encode, read_json, writer_lock, site_lock_path


def pinned_rows(cache, source, object_id, midpoint, targets):
    raw = read_raw(cache, source['sha256'])
    rows, metadata = parse_response(raw, object_id, midpoint, targets=targets)
    if any(source[key] != value for key, value in metadata.items()):
        raise DataError('Pinned Horizons provenance differs from published product')
    return rows


def modern_entries(index):
    """Select replay inputs after product authentication, with explicit errors."""
    if index['comet_contract_version'] == 2:
        return index['objects']
    entries = []
    for entry in index['objects']:
        matches = [segment for segment in entry['segments'] if segment['key'] == 'modern']
        if len(matches) != 1:
            raise DataError(f"{entry['id']}: expected exactly one modern segment for evidence replay")
        entries.append(dict(matches[0], id=entry['id'], name=entry['name']))
    return entries


def rebuild(directory, cache, output):
    directory, cache, output = Path(directory), Path(cache), Path(output)
    # Never let fixture output replace public products or retained scientific inputs.
    validate_append_path(output, label='Comet evidence output')
    validate_disjoint_paths(output, (directory.parent, cache), label='Comet evidence output')
    directory, cache, output = directory.resolve(), cache.resolve(), output.resolve()
    lock = site_lock_path(directory.parent)
    with writer_lock(lock):
        verified = verify_current_comets(directory)
        index = read_json(directory / verified['pin']['url'])
        # Historical quarter-point evidence is replayed by comet_history_candidate.py.
        entries = modern_entries(index)
        targets = {entry['id']: entry['name'] for entry in entries}
        evidence = {}
        for entry in entries:
            object_id = entry['id']
            rows = pinned_rows(cache, entry['provenance']['vectors'], object_id, False, targets)
            middle = pinned_rows(cache, entry['provenance']['midpoints'], object_id, True, targets)
            series = read_json(directory / entry['data']['url'])['series']
            serialized = serialize_states(rows)
            if serialized != series['states']:
                raise DataError(f'{object_id}: pinned vectors differ from serialized states')
            validation = entry['validation']
            measured = measure_interpolation(series, middle, object_id)
            if any(measured[key] != validation[key] for key in ('max_error_au', 'worst_jd', 'samples')):
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
    parser.add_argument('--directory', type=Path, default=ROOT / 'data/comets')
    parser.add_argument('--cache', type=Path, default=ROOT / '.data/horizons')
    parser.add_argument('--output', type=Path, default=ROOT / 'tests/fixtures/comet-midpoints.json.gz')
    args = parser.parse_args()
    try:
        print(json.dumps(rebuild(args.directory, args.cache, args.output)))
    except (OSError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
