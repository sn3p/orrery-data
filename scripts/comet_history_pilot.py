#!/usr/bin/env python3
"""Unpublished, offline-replayable experiment. Never writes the public data tree."""
import argparse
from datetime import date
import gzip
import hashlib
import json
import math
from pathlib import Path
import re
import sys
from urllib.parse import urlencode

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from orrery_data.horizons import download, ENDPOINT, MAX_RESPONSE
from orrery_data.comet_product import CONVENTIONS, interpolate, serialize_states, TOLERANCE
from orrery_data.storage import encode, loads_json, now, atomic_json, atomic_bytes
from orrery_data.formats import DataError

OUTPUT = ROOT / '.context/comet-history-pilot'
# Explicit records plus checked solution identities, never CAP.
CASES = {'1P': ('1P/Halley', '90000030', 'JPL#75', '1835-08-21'),
         '55P': ('55P/Tempel-Tuttle', '90000626', 'JPL#J985/69', '1865-12-22')}


def jd(day):
    return date.fromisoformat(day).toordinal() + 1721424.5


def query(record, start, count, offset=0):
    # Offset grids contain one reference in each state interval.
    first = start + offset
    last = start + 4 * (count - 1) - (4 - offset if offset else 0)
    params = dict(format='json', COMMAND=f"'{record};'", EPHEM_TYPE="'VECTORS'",
                  CENTER="'500@10'", START_TIME=f"'JD{first}'", STOP_TIME=f"'JD{last}'",
                  STEP_SIZE="'4 d'", TIME_TYPE="'UT'", OUT_UNITS="'AU-D'",
                  REF_PLANE="'ECLIPTIC'", REF_SYSTEM="'ICRF'", VEC_TABLE="'2'",
                  CSV_FORMAT="'YES'", VEC_CORR="'NONE'", OBJ_DATA="'YES'")
    return ENDPOINT + '?' + urlencode(params)


def parse(raw, name, solution, start, count):
    if len(raw) > MAX_RESPONSE:
        raise DataError('Oversized response')
    d = loads_json(raw)
    if d.get('error') or d.get('signature') not in [
        {'source': 'NASA/JPL Horizons API', 'version': v} for v in ('1.2', '1.3')]:
        raise DataError('API error/signature')
    s = d.get('result', '')
    if s.count('$$SOE') != 1 or s.count('$$EOE') != 1:
        raise DataError('Incomplete table')
    header, body = s.split('$$SOE'); body, _ = body.split('$$EOE')
    required = [r'Target body name:\s*' + re.escape(name) + r'\s+\{source: ' + re.escape(solution) + r'\}',
                r'Center body name:\s*Sun \(10\)', r'Center-site name:\s*BODY CENTER',
                r'Output units\s*:\s*AU-D\s*$', r'Output type\s*:\s*GEOMETRIC cartesian states\s*$',
                r'Output format\s*:\s*2 \(position and velocity\)\s*$',
                r'Reference frame\s*:\s*Ecliptic of J2000.0\s*$', r'JDUT\s*,',
                r'Step-size\s*:\s*5760 minutes\s*$']
    if not all(re.search(p, header, re.M) for p in required):
        raise DataError('Wrong identity/solution/conventions')
    rows = []
    for line in body.strip().splitlines():
        cols = [v.strip() for v in line.split(',')]
        if len(cols) != 9 or cols[-1] != '':
            raise DataError('Malformed vector')
        row = [float(cols[0]), *map(float, cols[2:8])]
        if not all(math.isfinite(v) for v in row) or row[0] != start + 4 * len(rows):
            raise DataError('Nonfinite or wrong grid')
        rows.append(row)
    if len(rows) != count:
        raise DataError('Incomplete grid')
    header = '\n'.join(line.rstrip() for line in header.splitlines()
                       if not line.startswith(('JPL/HORIZONS', 'Ephemeris /', 'Calling TDBMUT')))
    return rows, header


def acquire(url, acquire_online):
    cache = OUTPUT / 'raw'; cache.mkdir(parents=True, exist_ok=True)
    key = hashlib.sha256(url.encode()).hexdigest()
    record = cache / (key + '.json')
    if record.exists():
        info = json.loads(record.read_text()); raw = (cache / (info['sha256'] + '.raw')).read_bytes()
        if info['url'] != url or hashlib.sha256(raw).hexdigest() != info['sha256']:
            raise DataError('Corrupt cache')
    elif acquire_online:
        raw = download(url)
        info = dict(url=url, sha256=hashlib.sha256(raw).hexdigest(), retrieved_at=now())
        atomic_bytes(cache / (info['sha256'] + '.raw'), raw); atomic_json(record, info)
    else:
        raise DataError('Missing cached input: rerun explicitly with --acquire')
    return raw, info


def size(value):
    raw = (encode(value) + '\n').encode()
    return {'json_bytes': len(raw), 'gzip_bytes': len(gzip.compress(raw, mtime=0)),
            'sha256': hashlib.sha256(raw).hexdigest()}


def validate_metadata(directory):
    """Check research records without declaring a public schema."""
    value = loads_json((directory / 'discoveries.json').read_bytes())
    if [o['id'] for o in value['objects']] != ['1P', '2P', '55P', '9P', '10P', '67P']:
        raise DataError('Unexpected metadata inventory')
    kinds = {'discovery', 'independent_discovery', 'recovery', 'earlier_sighting',
             'photographic_observation', 'discovery_recognition'}
    for obj in value['objects']:
        provenance = obj['catalogue_provenance']
        raw = (directory / 'sources' / (obj['id'] + '-sbdb.json')).read_bytes()
        if (hashlib.sha256(raw).hexdigest() != provenance['sha256'] or
                loads_json(raw)['discovery'] != obj['catalogue_discovery']):
            raise DataError('Catalogue assertion differs from retained source')
        for event in obj['events']:
            d = event['date']
            if (event['kind'] not in kinds or not event['sources'] or
                    any(s not in value['sources'] for s in event['sources']) or
                    d['year_numbering'] != 'astronomical' or
                    d['calendar'] not in ('gregorian', 'historical-unspecified')):
                raise DataError('Invalid event semantics/source/calendar')
            precision = d['precision']
            if (type(d['year']) is not int or precision not in ('year', 'month', 'day') or
                    (precision == 'year' and (d['month'] is not None or d['day'] is not None)) or
                    (precision == 'month' and d['day'] is not None) or
                    (precision in ('month', 'day') and (type(d['month']) is not int or not 1 <= d['month'] <= 12)) or
                    (precision == 'day' and type(d['day']) is not int)):
                raise DataError('Invalid date precision')
            if precision == 'day':
                if d['calendar'] != 'gregorian' or d['year'] < 1:
                    raise DataError('Exact historical date requires calendar conversion')
                date(d['year'], d['month'], d['day'])
    return value


def model_identity(header):
    # Initial conditions, non-gravitational model, perturbers and EOP must agree.
    # Only request endpoints differ between the independent offset grids.
    return '\n'.join(line for line in header.splitlines()
                     if not line.startswith(('Start time', 'Stop  time')))


def velocity(series, time):
    """Derivative of the cubic Hermite interpolant, AU/day."""
    offset = (time - series['start']) / series['step']
    if not 0 <= offset <= len(series['states']) - 1:
        raise DataError('Velocity outside coverage')
    i = min(math.floor(offset), len(series['states']) - 2)
    t = offset - i; h = series['step']
    a, b = series['states'][i:i+2]
    return [((6*t*t-6*t)*a[k] + (3*t*t-4*t+1)*h*a[k+3]
             + (-6*t*t+6*t)*b[k] + (3*t*t-2*t)*h*b[k+3]) / h for k in range(3)]


def run(acquire_online=False):
    metadata = validate_metadata(ROOT / 'research/comet-history')
    OUTPUT.mkdir(parents=True, exist_ok=True)
    results = {}
    index = json.loads((ROOT / 'data/comets' / json.loads((ROOT / 'data/comets/latest.json').read_text())['index']['url']).read_text())
    for ident, (name, record, solution, day) in CASES.items():
        start = jd(day); count = int((jd('2099-12-30') - start) // 4) + 1
        url = query(record, start, count)
        raw, info = acquire(url, acquire_online)
        rows, header = parse(raw, name, solution, start, count)
        value = {'experimental': True, 'id': ident, 'conventions': CONVENTIONS,
                 'series': {'start': start, 'step': 4, 'states': serialize_states(rows)}}
        # Roundtrip final serialized bytes before numerical validation.
        value = json.loads(encode(value)); series = value['series']
        checks = []; provenance = {'vectors': {**info, 'header': header}}
        for offset in (1, 2, 3):
            raw, info = acquire(query(record, start, count, offset), acquire_online)
            refs, ref_header = parse(raw, name, solution, start + offset, count - 1)
            if model_identity(header) != model_identity(ref_header):
                raise DataError('Fitted initial conditions changed between requests')
            errors = [math.dist(interpolate(series, r[0]), r[1:4]) for r in refs]
            worst = max(range(len(errors)), key=errors.__getitem__)
            checks.append(dict(offset_days=offset, samples=len(refs), max_error_au=errors[worst], worst_jd=refs[worst][0]))
            provenance[str(offset)] = {**info, 'header': ref_header}
        # Measure historical-only payload including one state beyond 1980 for overlap.
        history_count = math.ceil((jd('1980-01-01') - start) / 4) + 1
        history = {**value, 'series': {**series, 'states': series['states'][:history_count]}}
        old = next(e for e in index['objects'] if e['id'] == ident)
        old_raw = (ROOT / 'data/comets' / old['data']['url']).read_bytes()
        if hashlib.sha256(old_raw).hexdigest() != old['data']['sha256']:
            raise DataError('Corrupt baseline')
        current = json.loads(old_raw)['series']
        # Compare every overlapping baseline state to the new interpolant, including 1980 seam.
        overlap = [(current['start'] + i * 4, state) for i, state in enumerate(current['states'])
                   if current['start'] + i * 4 <= start + (count - 1) * 4]
        diffs = [math.dist(interpolate(series, t), state[:3]) for t, state in overlap]
        result = dict(solution=solution, start=day, start_jd=start, end_jd=start+(count-1)*4,
                      states=count, history_states=history_count,
                      history_end_jd=start+(history_count-1)*4, checks=checks,
                      tolerance_au=TOLERANCE, passed=all(c['max_error_au'] <= TOLERANCE for c in checks),
                      full_size=size(value), history_size=size(history), baseline_size=size(json.loads(old_raw)),
                      overlap_samples=len(overlap), overlap_max_difference_au=max(diffs),
                      seam_position_difference_au=diffs[0],
                      seam_velocity_difference_au_per_day=math.dist(velocity(series, current['start']), current['states'][0][3:]),
                      provenance=provenance)
        atomic_json(OUTPUT / f'{ident}-full.json', value)
        atomic_json(OUTPUT / f'{ident}-history.json', history)
        results[ident] = result
        print(ident, encode({k:v for k,v in result.items() if k != 'provenance'}), flush=True)
    atomic_json(OUTPUT / 'results.json', results)
    atomic_json(OUTPUT / 'metadata-size.json', size(metadata))
    if not all(r['passed'] for r in results.values()):
        raise DataError('Pilot exceeds interpolation target; evidence retained')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--acquire', action='store_true', help='Permit serial JPL downloads for missing cached queries')
    run(parser.parse_args().acquire)
