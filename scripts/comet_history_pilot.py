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
import subprocess
from urllib.parse import urlencode

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from orrery_data.horizons import download, ENDPOINT, parse_vector_response, read_raw
from orrery_data.comet_product import CONVENTIONS, interpolate, interpolate_state, serialize_states, TOLERANCE
from orrery_data.storage import encode, loads_json, now, atomic_json, atomic_bytes, writer_lock, utc_timestamp
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
    def calendar(julian):
        day = date.fromordinal(int(julian - 1721424.5))
        months = ('Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec')
        return f'{day.year:04d}-{months[day.month-1]}-{day.day:02d}'
    rows, provenance = parse_vector_response(raw, name=name, solution=solution,
        start=start, step=4, count=count, start_date=calendar(start),
        stop_date=calendar(start + 4 * (count - 1)))
    return rows, provenance['header']


def acquire(url, acquire_online, validate):
    cache = OUTPUT / 'raw'
    with writer_lock(cache):
        record = cache / (hashlib.sha256(url.encode()).hexdigest() + '.json')
        if record.exists() or record.is_symlink():
            try:
                if record.is_symlink() or not record.is_file() or record.stat().st_size > 4096:
                    raise DataError('Invalid cache record')
                info = loads_json(record.read_bytes())
                if not isinstance(info, dict) or set(info) != {'url', 'sha256', 'retrieved_at'} or info['url'] != url:
                    raise DataError('Invalid cache record')
                utc_timestamp(info['retrieved_at'], 'Pilot cache')
                raw = read_raw(cache, info['sha256'])
                parsed = validate(raw)
                return raw, info, parsed
            except (OSError, ValueError, TypeError, KeyError, DataError) as exc:
                if not acquire_online:
                    raise DataError('Corrupt cache') from exc
                # Explicit acquisition may repair corrupt cached inputs; only
                # install the replacement after complete scientific validation.
        elif not acquire_online:
            raise DataError('Missing cached input: rerun explicitly with --acquire')
        raw = download(url)
        parsed = validate(raw)
        info = dict(url=url, sha256=hashlib.sha256(raw).hexdigest(), retrieved_at=now())
        atomic_bytes(cache / (info['sha256'] + '.raw'), raw)
        atomic_json(record, info)
        return raw, info, parsed


def raw_size(raw):
    return {'json_bytes': len(raw), 'gzip_bytes': len(gzip.compress(raw, mtime=0)),
            'sha256': hashlib.sha256(raw).hexdigest()}


def size(value):
    return raw_size((encode(value) + '\n').encode())


def _validate_metadata(directory):
    """Check research records without declaring a public schema."""
    value = loads_json((directory / 'discoveries.json').read_bytes())
    if [o['id'] for o in value['objects']] != ['1P', '2P', '55P', '9P', '10P', '67P']:
        raise DataError('Unexpected metadata inventory')
    kinds = {'discovery', 'independent_discovery', 'recovery', 'earlier_sighting',
             'photographic_observation', 'discovery_recognition'}
    for obj in value['objects']:
        provenance = obj['catalogue_provenance']
        expected = 'sources/' + obj['id'] + '-sbdb.json'
        if provenance['raw_file'] != expected:
            raise DataError('Unexpected metadata raw-file path')
        raw = (directory / expected).read_bytes()
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


def validate_metadata(directory):
    try:
        return _validate_metadata(directory)
    except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
        raise DataError('Invalid history metadata') from exc


def baseline_reference(ref, prefix, limit):
    if (not isinstance(ref, dict) or set(ref) != {'url', 'sha256', 'bytes'} or
            not isinstance(ref['sha256'], str) or not re.fullmatch(r'[a-f0-9]{64}', ref['sha256']) or
            ref['url'] != prefix + ref['sha256'] + '.json' or
            type(ref['bytes']) is not int or not 0 < ref['bytes'] <= limit):
        raise DataError('Invalid baseline reference')


def read_baseline(commit, ref, prefix, limit):
    baseline_reference(ref, prefix, limit)
    cache = OUTPUT / 'baseline'; cache.mkdir(parents=True, exist_ok=True)
    path = cache / (ref['sha256'] + '.json')
    if path.exists():
        raw = path.read_bytes()
    else:
        # The tracked pin selects immutable Git bytes, never latest.json.
        raw = subprocess.run(['git', 'show', f"{commit}:data/comets/{ref['url']}"],
                             cwd=ROOT, check=True, capture_output=True).stdout
    if len(raw) != ref['bytes'] or hashlib.sha256(raw).hexdigest() != ref['sha256']:
        raise DataError('Corrupt baseline')
    if not path.exists():
        atomic_bytes(path, raw)
    return raw


def load_baseline():
    try:
        pin = loads_json((ROOT / 'research/comet-history/baseline.json').read_bytes())
        if not re.fullmatch(r'[a-f0-9]{40}', pin['commit']):
            raise DataError('Invalid baseline commit')
        raw = read_baseline(pin['commit'], pin['index'], 'index-', 256 * 1024)
        index = loads_json(raw)
        objects = {}
        for ident in CASES:
            entry = next(e for e in index['objects'] if e['id'] == ident)
            raw = read_baseline(pin['commit'], entry['data'], 'objects/', 2 * 1024 * 1024)
            objects[ident] = (loads_json(raw)['series'], raw_size(raw))
        return pin, objects
    except DataError:
        raise
    except (OSError, ValueError, TypeError, KeyError, AttributeError, StopIteration, subprocess.CalledProcessError) as exc:
        raise DataError('Invalid or unavailable pinned baseline') from exc


BASELINE_POSITION_LIMIT = TOLERANCE
# Numerical compatibility guard: one interpolation-position budget per grid step.
# This is not a physical velocity-uncertainty estimate.
BASELINE_VELOCITY_LIMIT = TOLERANCE / 4


def acceptance(checks, position_difference, velocity_difference):
    return (all(math.isfinite(c['max_error_au']) and c['max_error_au'] <= TOLERANCE for c in checks)
            and math.isfinite(position_difference) and position_difference <= BASELINE_POSITION_LIMIT
            and math.isfinite(velocity_difference) and velocity_difference <= BASELINE_VELOCITY_LIMIT)


def model_identity(header):
    # Initial conditions, non-gravitational model, perturbers and EOP must agree.
    # Only request endpoints differ between the independent offset grids.
    return '\n'.join(line for line in header.splitlines()
                     if not line.startswith(('Start time', 'Stop  time')))


def velocity(series, time):
    return interpolate_state(series, time)[1]


def _run_locked(acquire_online=False):
    metadata = validate_metadata(ROOT / 'research/comet-history')
    OUTPUT.mkdir(parents=True, exist_ok=True)
    results = {}
    baseline_pin, baseline = load_baseline()
    for ident, (name, record, solution, day) in CASES.items():
        start = jd(day); count = int((jd('2099-12-30') - start) // 4) + 1
        url = query(record, start, count)
        raw, info, (rows, header) = acquire(url, acquire_online,
            lambda raw: parse(raw, name, solution, start, count))
        value = {'experimental': True, 'id': ident, 'conventions': CONVENTIONS,
                 'series': {'start': start, 'step': 4, 'states': serialize_states(rows)}}
        # Roundtrip final serialized bytes before numerical validation.
        value = json.loads(encode(value)); series = value['series']
        checks = []; provenance = {'vectors': {**info, 'header': header}}
        for offset in (1, 2, 3):
            def validate_reference(raw):
                refs, ref_header = parse(raw, name, solution, start + offset, count - 1)
                if model_identity(header) != model_identity(ref_header):
                    raise DataError('Scientific model changed between requests')
                return refs, ref_header
            raw, info, (refs, ref_header) = acquire(query(record, start, count, offset), acquire_online, validate_reference)
            errors = [math.dist(interpolate(series, r[0]), r[1:4]) for r in refs]
            worst = max(range(len(errors)), key=errors.__getitem__)
            checks.append(dict(offset_days=offset, samples=len(refs), max_error_au=errors[worst], worst_jd=refs[worst][0]))
            provenance[str(offset)] = {**info, 'header': ref_header}
        # Measure historical-only payload including one state beyond 1980 for overlap.
        history_count = math.ceil((jd('1980-01-01') - start) / 4) + 1
        history = {**value, 'series': {**series, 'states': series['states'][:history_count]}}
        current, baseline_size = baseline[ident]
        # Compare every overlapping baseline state to the new interpolant, including 1980 seam.
        overlap = [(current['start'] + i * 4, state) for i, state in enumerate(current['states'])
                   if current['start'] + i * 4 <= start + (count - 1) * 4]
        diffs = [math.dist(interpolate(series, t), state[:3]) for t, state in overlap]
        velocity_diffs = [math.dist(velocity(series, t), state[3:]) for t, state in overlap]
        result = dict(baseline=baseline_pin, solution=solution, start=day, start_jd=start, end_jd=start+(count-1)*4,
                      states=count, history_states=history_count,
                      history_end_jd=start+(history_count-1)*4, checks=checks,
                      tolerance_au=TOLERANCE, passed=acceptance(checks, max(diffs), max(velocity_diffs)),
                      baseline_position_limit_au=BASELINE_POSITION_LIMIT, baseline_velocity_limit_au_per_day=BASELINE_VELOCITY_LIMIT,
                      overlap_max_velocity_difference_au_per_day=max(velocity_diffs),
                      full_size=size(value), history_size=size(history), baseline_size=baseline_size,
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
        raise DataError('Pilot exceeds interpolation or baseline compatibility limit; evidence retained')


def run(acquire_online=False):
    with writer_lock(OUTPUT):
        return _run_locked(acquire_online)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--acquire', action='store_true', help='Permit serial JPL downloads for missing cached queries')
    run(parser.parse_args().acquire)
