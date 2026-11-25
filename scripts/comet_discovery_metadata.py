#!/usr/bin/env python3
"""Build an unpublished discovery sidecar from retained, attributed evidence."""
import argparse
import calendar
import gzip
import hashlib
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / 'research/comet-discovery'


def encoded(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')) + '\n').encode()


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def structured_date(value):
    """Never infer dates from designations, orbital epochs or first observations."""
    if not isinstance(value, str) or not value:
        return None
    match = re.fullmatch(r'([0-9]{4})(?:-([A-Za-z]{3}|\?\?)(?:-([0-9]{2}|\?\?))?)?', value)
    if not match:
        return None
    year, month, day = match.groups()
    year = int(year)
    months = list(calendar.month_abbr)
    if month not in (None, '??', *months[1:]):
        return None
    month = months.index(month) if month in months[1:] else None
    day = int(day) if day and day not in ('??', '00') else None
    # Unspecified calendar: keep dates possible in either Julian or Gregorian.
    # Acceptance does not identify a calendar or convert to an instant.
    days = [31, 29 if year % 4 == 0 else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
    if not year or (day is not None and (month is None or not 1 <= day <= days[month - 1])):
        return None
    return dict(year=year, month=month, day=day, precision='day' if day else 'month' if month else 'year',
                calendar='historical-unspecified', year_numbering='astronomical')


def add_source(sources, key, source):
    if key in sources and sources[key] != source:
        raise ValueError('Conflicting source: ' + key)
    sources[key] = source


def build():
    root = ROOT / 'data/comets'
    descriptor = json.loads((root / 'latest.json').read_bytes())
    if descriptor != json.loads((EVIDENCE / 'baseline.json').read_bytes()):
        raise ValueError('Directory changed; reassess discovery metadata explicitly')
    raw = (root / descriptor['index']['url']).read_bytes()
    if sha(raw) != descriptor['index']['sha256']:
        raise ValueError('Directory digest mismatch')
    index = json.loads(raw)
    legacy_raw = (root / index['legacy']['url']).read_bytes()
    if sha(legacy_raw) != index['legacy']['sha256']:
        raise ValueError('Legacy digest mismatch')
    legacy = json.loads(legacy_raw)
    evidence = json.loads(gzip.decompress((EVIDENCE / 'sbdb-responses.json.gz').read_bytes()))
    curated = json.loads((EVIDENCE / 'events.json').read_bytes())
    sources = dict(legacy['sources'])
    for key, source in curated['sources'].items():
        add_source(sources, key, source)
    rows = []
    originals = {o['id']: o['discovery'] for o in legacy['objects']}
    for obj in index['objects']:
        identity = obj['id']
        if identity in originals:
            rows.append(dict(id=identity, status='preserved', discovery=originals[identity]))
            continue
        record = evidence.get(identity)
        catalogue, provenance = None, None
        if record:
            response = record['response']
            if 'jpl:' + str(response.get('object', {}).get('spkid')) not in obj['source_ids']:
                raise ValueError('SBDB identity mismatch: ' + identity)
            catalogue = response.get('discovery') or None
            key = 'sbdb-' + identity
            add_source(sources, key, dict(url=record['url'], checked_on=record['retrieved_at'][:10]))
            provenance = dict(source=key, sha256=sha(encoded(response)), representation='canonical-json',
                              retrieved_at=record['retrieved_at'])
        events = curated['objects'].get(identity, [])
        discovery = dict(id=identity, catalogue_discovery=catalogue, catalogue_provenance=provenance,
                         catalogue_date=structured_date((catalogue or {}).get('date')),
                         events=events, event_inventory_complete=False)
        status = 'sourced' if (catalogue and any(catalogue.values())) or events else 'source-empty' if record else 'outside-source-scope'
        rows.append(dict(id=identity, status=status, discovery=discovery))
    return dict(discovery_metadata_version=1, directory_sha256=sha(raw), sources=sources, objects=rows)


def write_candidate(output):
    namespace = ROOT.resolve() / '.context/comet-candidates'
    output = Path(output)
    if (namespace.parent.is_symlink() or namespace.is_symlink()
            or not output.resolve().is_relative_to(namespace)
            or output.resolve() == namespace):
        raise ValueError('Output must be inside workspace .context/comet-candidates')
    raw = encoded(build())
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        if output.read_bytes() != raw:
            raise ValueError('Existing metadata candidate differs')
    else:
        with output.open('xb') as stream:
            stream.write(raw)
    return dict(sha256=sha(raw), bytes=len(raw), path=str(output))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(write_candidate(args.output)))
