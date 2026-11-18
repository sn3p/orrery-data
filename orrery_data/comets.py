"""Independent comet v1 projection and strict offline verification."""

import math
from pathlib import Path
import re
import stat
import tempfile

from .browser import check_reference, reference, sync_candidate
from .formats import DataError
from .horizons import TARGETS, START, STEP, COUNT, acquire, query
from .paths import validate_disjoint_paths, validate_writable_path
from .storage import read_json, write_json, verify_file, writer_lock, utc_timestamp

CONVENTIONS = {'center': 'Sun', 'frame': 'Ecliptic J2000', 'time': 'UT',
               'position_units': 'AU', 'velocity_units': 'AU/day',
               'correction': 'NONE', 'interpolation': 'cubic-hermite', 'encoding': 'uniform-state-v1'}
MAX_OBJECT_BYTES = 2 * 1024 * 1024
MAX_INDEX_BYTES = 256 * 1024
TOLERANCE = 0.000052
MANAGED = re.compile(r'(?:latest\.json|index-[a-f0-9]{64}\.json|objects/[a-f0-9]{64}\.json)')


def fields(value, names, label):
    if not isinstance(value, dict) or set(value) != set(names):
        raise DataError(f'Invalid {label} fields')


def number(value):
    return type(value) in (int, float) and math.isfinite(value)


def managed_files(directory):
    directory = Path(directory)
    if not directory.exists() and not directory.is_symlink():
        return set()
    if not stat.S_ISDIR(directory.lstat().st_mode):
        raise DataError('Comet output must be a real directory')
    names = set()
    for path in directory.iterdir():
        entries = list(path.iterdir()) if path.name == 'objects' and stat.S_ISDIR(path.lstat().st_mode) else [path]
        for item in entries:
            name = item.relative_to(directory).as_posix()
            if not stat.S_ISREG(item.lstat().st_mode) or not MANAGED.fullmatch(name):
                raise DataError(f'Unexpected comet output entry: {name}')
            names.add(name)
    return names


def validate_series(series):
    fields(series, ('start', 'step', 'states'), 'series')
    if series['start'] != START or series['step'] != STEP:
        raise DataError('Unsupported comet grid')
    states = series['states']
    if not isinstance(states, list) or len(states) != COUNT:
        raise DataError('Invalid comet state count')
    if any(not isinstance(row, list) or len(row) != 6 or not all(number(v) for v in row) for row in states):
        raise DataError('Invalid comet state')


def interpolate(series, jd):
    offset = (jd - series['start']) / series['step']
    states = series['states']
    if not number(jd) or offset < 0 or offset > len(states) - 1:
        raise DataError('Date outside comet coverage')
    index = min(int(offset), len(states) - 2)
    t = offset - index
    a, b = states[index:index + 2]
    h = series['step']
    return [(2*t**3-3*t*t+1)*a[k] + (t**3-2*t*t+t)*h*a[k+3]
            + (-2*t**3+3*t*t)*b[k] + (t**3-t*t)*h*b[k+3] for k in range(3)]


def validate_object(value, object_id):
    fields(value, ('comet_contract_version', 'id', 'conventions', 'series'), 'object')
    if (type(value['comet_contract_version']) is not int or value['comet_contract_version'] != 1
            or value['id'] != object_id or value['conventions'] != CONVENTIONS):
        raise DataError('Unsupported comet object contract/identity/conventions')
    validate_series(value['series'])


def read_reference(directory, ref, prefix, cap):
    fields(ref, ('url', 'sha256', 'bytes'), 'reference')
    if not isinstance(ref['sha256'], str) or not re.fullmatch(r'[a-f0-9]{64}', ref['sha256']):
        raise DataError('Invalid comet reference hash')
    check_reference(ref, prefix, '.json')
    if type(ref['bytes']) is not int or not 1 <= ref['bytes'] <= cap:
        raise DataError('Invalid comet reference size')
    verify_file(directory / ref['url'], ref)
    return read_json(directory / ref['url'])


def verify_comets(directory, *, allow_orphans=False):
    directory = Path(directory)
    names = managed_files(directory)
    if 'latest.json' not in names or (directory / 'latest.json').stat().st_size > 4096:
        raise DataError('Missing or oversized comet descriptor')
    latest = read_json(directory / 'latest.json')
    fields(latest, ('comet_contract_version', 'index'), 'descriptor')
    if type(latest['comet_contract_version']) is not int or latest['comet_contract_version'] != 1:
        raise DataError('Unsupported comet contract')
    index = read_reference(directory, latest['index'], 'index-', MAX_INDEX_BYTES)
    fields(index, ('comet_contract_version', 'objects'), 'index')
    if type(index['comet_contract_version']) is not int or index['comet_contract_version'] != 1:
        raise DataError('Unsupported comet index')
    if not isinstance(index['objects'], list) or [v.get('id') for v in index['objects'] if isinstance(v, dict)] != list(TARGETS):
        raise DataError('Comet v1 requires the three reviewed seed objects in order')
    expected = {'latest.json', latest['index']['url']}
    for entry in index['objects']:
        fields(entry, ('id', 'name', 'coverage', 'data', 'provenance', 'validation'), 'catalogue entry')
        if entry['name'] != TARGETS[entry['id']] or entry['coverage'] != [START, START + STEP * (COUNT - 1)]:
            raise DataError('Invalid comet metadata/coverage')
        provenance = entry['provenance']
        fields(provenance, ('vectors', 'midpoints'), 'provenance')
        for kind, source in provenance.items():
            fields(source, ('url', 'sha256', 'retrieved_at', 'target', 'solution', 'header', 'api_version'), 'source')
            if any(not isinstance(v, str) or not v for v in source.values()):
                raise DataError('Invalid comet provenance')
            utc_timestamp(source['retrieved_at'], 'Horizons retrieval')
            if source['url'] != query(entry['id'], kind == 'midpoints') or source['api_version'] not in ('1.2', '1.3'):
                raise DataError('Invalid source query or API version')
            if source['target'] != entry['name'] or not re.fullmatch(r'JPL#[A-Za-z0-9/._-]+', source['solution']):
                raise DataError('Invalid source identity')
            if not re.fullmatch(r'[a-f0-9]{64}', source['sha256']):
                raise DataError('Invalid raw response hash')
        if provenance['vectors']['solution'] != provenance['midpoints']['solution']:
            raise DataError('Mixed Horizons solutions')
        validation = entry['validation']
        fields(validation, ('method', 'samples', 'max_error_au', 'worst_jd', 'tolerance_au', 'references'), 'validation')
        if (validation['method'] != 'all-midpoints-final-serialized' or validation['samples'] != COUNT - 1
                or validation['tolerance_au'] != TOLERANCE or not number(validation['max_error_au'])
                or not 0 <= validation['max_error_au'] <= TOLERANCE):
            raise DataError('Invalid numerical validation')
        value = read_reference(directory, entry['data'], 'objects/', MAX_OBJECT_BYTES)
        validate_object(value, entry['id'])
        samples = validation['references']
        if not isinstance(samples, list) or not samples:
            raise DataError('Missing independent references')
        for row in samples:
            if not isinstance(row, list) or len(row) != 4 or not all(number(v) for v in row):
                raise DataError('Invalid independent reference')
            if math.dist(interpolate(value['series'], row[0]), row[1:]) > TOLERANCE:
                raise DataError('Serialized interpolation exceeds tolerance')
        if validation['worst_jd'] not in [row[0] for row in samples]:
            raise DataError('Missing worst reference')
        expected.add(entry['data']['url'])
    if not expected <= names or (not allow_orphans and names != expected):
        raise DataError('Comet inventory differs from pinned index')
    return {'path': str(directory), 'pin': latest['index'], 'objects': len(index['objects']),
            'files': len(names), 'bytes': sum((directory / n).stat().st_size for n in names)}


def build_candidate(records, stage, previous=None):
    (stage / 'objects').mkdir()
    index = {'comet_contract_version': 1, 'objects': []}
    for object_id, name in TARGETS.items():
        rows, source = records[object_id + '-vectors']
        middle, check = records[object_id + '-midpoints']
        if source['solution'] != check['solution']:
            raise DataError('Horizons solution changed during acquisition; refresh both queries')
        series = {'start': START, 'step': STEP,
                  'states': [[float(f'{v:.12g}') for v in row[1:]] for row in rows]}
        value = {'comet_contract_version': 1, 'id': object_id, 'conventions': CONVENTIONS, 'series': series}
        temporary = stage / 'object.json'
        write_json(temporary, value)
        # Measure the bytes actually published, rather than the pre-rounding data.
        series = read_json(temporary)['series']
        validate_series(series)
        errors = [math.dist(interpolate(series, row[0]), row[1:4]) for row in middle]
        worst = max(range(len(errors)), key=errors.__getitem__)
        if errors[worst] > TOLERANCE:
            raise DataError(f'{object_id}: interpolation exceeds {TOLERANCE} AU')
        ref = reference(temporary, 'objects/', '.json')
        temporary.rename(stage / ref['url'])
        entry = {'id': object_id, 'name': name, 'coverage': [START, START + STEP * (COUNT - 1)],
                 'data': ref, 'provenance': {'vectors': source, 'midpoints': check},
                 'validation': {'method': 'all-midpoints-final-serialized', 'samples': len(errors),
                                'max_error_au': errors[worst], 'worst_jd': middle[worst][0],
                                'tolerance_au': TOLERANCE,
                                'references': [middle[i][:4] for i in sorted({0, len(errors)-1, worst, *range(0, len(errors), 400)})]}}
        # Raw hashes/banner clocks identify a retrieval, not a scientific solution.
        # Reuse the prior acquisition provenance only when all scientific headers,
        # queries, serialized states and numerical results are identical.
        if previous:
            old = next(v for v in previous['objects'] if v['id'] == object_id)
            def scientific(item):
                return {k: v for k, v in item.items() if k not in ('sha256', 'retrieved_at')}
            if (old['data'] == ref and old['validation'] == entry['validation']
                    and all(scientific(old['provenance'][k]) == scientific(v) for k, v in entry['provenance'].items())):
                entry['provenance'] = old['provenance']
        index['objects'].append(entry)
    temporary = stage / 'index.json'
    write_json(temporary, index)
    pin = reference(temporary, 'index-', '.json')
    temporary.rename(stage / pin['url'])
    write_json(stage / 'latest.json', {'comet_contract_version': 1, 'index': pin})
    verify_comets(stage)


def refresh_comets(cache, output, *, refresh=False, offline=False):
    cache, output = Path(cache), Path(output)
    validate_writable_path(output, label='Comet output')
    validate_disjoint_paths(output, (cache,), label='Comet output')
    managed_files(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    # When installed at data/comets, share the asteroid writer's site lock.
    lock = output.parent.parent / ('.' + output.parent.name + '-browser-lock')
    with writer_lock(lock):
        previous = None
        if (output / 'latest.json').exists():
            # Managed orphans left after pointer installation can be pruned by rerun.
            verify_comets(output, allow_orphans=True)
            pin = read_json(output / 'latest.json')['index']
            previous = read_reference(output, pin, 'index-', MAX_INDEX_BYTES)
        records = acquire(cache, refresh=refresh, offline=offline)
        with tempfile.TemporaryDirectory(prefix='.comets-', dir=output.parent.parent) as temp:
            stage = Path(temp)
            build_candidate(records, stage, previous)
            changes = sync_candidate(stage, output, inventory=managed_files)
            result = verify_comets(output)
    return {**result, 'status': 'updated' if any(changes[k] for k in ('added', 'changed', 'deleted')) else 'unchanged',
            'changes': changes}
