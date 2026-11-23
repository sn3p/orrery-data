"""Shared comet verification, numerical projection and atomic publication."""

import math
from functools import partial
from pathlib import Path
import re
import tempfile

from .browser import check_reference, reference, sync_candidate
from .formats import DataError
from .contracts import obj, array, rule, literal, TEXT, NONEMPTY, FILE_FIELDS, UTC, INDEX_NUMBER
from . import horizons
from .horizons import START, STEP, COUNT, query
from .paths import validate_disjoint_paths, validate_writable_path, managed_tree, validate_same_filesystem
from .storage import read_json, loads_json, write_json, verify_file, writer_lock, site_lock_path

CONVENTIONS = {'center': 'Sun', 'frame': 'Ecliptic J2000', 'time': 'UT',
               'position_units': 'AU', 'velocity_units': 'AU/day',
               'correction': 'NONE', 'interpolation': 'cubic-hermite', 'encoding': 'uniform-state-v1'}
MAX_OBJECT_BYTES = 2 * 1024 * 1024
MAX_INDEX_BYTES = 256 * 1024
TOLERANCE = 0.000052
MANAGED = re.compile(r'(?:latest\.json|index-[a-f0-9]{64}\.json|objects/[a-f0-9]{64}\.json)')


def number(value):
    return type(value) in (int, float) and math.isfinite(value)


REFERENCE = obj({'url': TEXT, **FILE_FIELDS})
SERIES = obj({'start': literal(START), 'step': literal(STEP), 'states': array(
    rule(lambda row: isinstance(row, list) and len(row) == 6 and all(number(v) for v in row), 'six finite numbers'))})
SOURCE = obj({**{key: NONEMPTY for key in ('url', 'sha256', 'target', 'solution', 'header', 'api_version')},
              'retrieved_at': UTC})
VALIDATION = obj({'method': literal('all-midpoints-final-serialized'), 'samples': literal(COUNT - 1),
                  'max_error_au': INDEX_NUMBER, 'worst_jd': INDEX_NUMBER, 'tolerance_au': literal(TOLERANCE),
                  'references': array(rule(lambda row: isinstance(row, list) and len(row) == 4
                                           and all(number(v) for v in row), 'JD and three finite positions'))})
ENTRY = obj({'id': NONEMPTY, 'name': NONEMPTY, 'coverage': array(INDEX_NUMBER), 'data': REFERENCE,
             'provenance': obj({'vectors': SOURCE, 'midpoints': SOURCE}), 'validation': VALIDATION})


class CometContract:
    """Wire version and optional fixed selection, independent of acquisition policy."""

    def __init__(self, version, fixed_targets=None):
        self.version = version
        self.fixed_targets = dict(fixed_targets) if fixed_targets is not None else None
        self.object_schema = obj({'comet_contract_version': literal(version), 'id': NONEMPTY,
                           'conventions': obj({key: literal(value) for key, value in CONVENTIONS.items()}),
                           'series': SERIES})
        self.latest_schema = obj({'comet_contract_version': literal(version), 'index': REFERENCE})
        self.index_schema = obj({'comet_contract_version': literal(version), 'objects': array(ENTRY)})

    def validate_selection(self, entries):
        if self.fixed_targets is not None:
            if [entry['id'] for entry in entries] != list(self.fixed_targets):
                raise DataError(f'Comet v{self.version} requires fixed selection: ' + ', '.join(self.fixed_targets))
        elif not entries:
            raise DataError('Comet catalogue must not be empty')

    def valid_id(self, object_id):
        return valid_id(object_id) and (self.fixed_targets is None or object_id in self.fixed_targets)

    def valid_name(self, name, object_id):
        if self.fixed_targets is not None:
            return name == self.fixed_targets.get(object_id)
        return valid_name(name, object_id)


def managed_files(directory):
    return managed_tree(directory, MANAGED, children=('objects',), label='Comet output')


def validate_series(series):
    SERIES(series, 'series')
    states = series['states']
    if len(states) != COUNT:
        raise DataError('Invalid comet state count')


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


def valid_id(value):
    return isinstance(value, str) and re.fullmatch(r'[1-9][0-9]*P', value) is not None


# Explicit union of ECMAScript/Python edge whitespace; neither runtime's trim policy.
EDGE_WHITESPACE = ('\u0009\u000a\u000b\u000c\u000d\u001c\u001d\u001e\u001f '
                   '\u0085\u00a0\u1680\u2000\u2001\u2002\u2003\u2004\u2005'
                   '\u2006\u2007\u2008\u2009\u200a\u2028\u2029\u202f\u205f\u3000\ufeff')


def valid_name(name, object_id):
    return (isinstance(name, str) and name.startswith(object_id + '/')
            and len(name) > len(object_id) + 1 and name == name.strip(EDGE_WHITESPACE)
            and not any(ord(c) < 32 or ord(c) == 127 for c in name))


def validate_object(value, object_id, *, contract):
    contract.object_schema(value, 'object')
    if not contract.valid_id(object_id) or value['id'] != object_id or len(value['series']['states']) != COUNT:
        raise DataError('Invalid comet identity or state count')


def read_reference(directory, ref, prefix, cap):
    REFERENCE(ref, 'reference')
    if not isinstance(ref['sha256'], str) or not re.fullmatch(r'[a-f0-9]{64}', ref['sha256']):
        raise DataError('Invalid comet reference hash')
    check_reference(ref, prefix, '.json')
    if type(ref['bytes']) is not int or not 1 <= ref['bytes'] <= cap:
        raise DataError('Invalid comet reference size')
    verify_file(directory / ref['url'], ref)
    return read_json(directory / ref['url'])


def inspect_comets(directory, *, contract, allow_orphans=False, descriptor=None):
    """Validate a product and return its report and the same validated index."""
    directory = Path(directory)
    names = managed_files(directory)
    if 'latest.json' not in names or (directory / 'latest.json').stat().st_size > 4096:
        raise DataError('Missing or oversized comet descriptor')
    latest = (read_json(directory / 'latest.json') if descriptor is None
              else loads_json(descriptor))
    contract.latest_schema(latest, 'descriptor')
    index = read_reference(directory, latest['index'], 'index-', MAX_INDEX_BYTES)
    contract.index_schema(index, 'index')
    contract.validate_selection(index['objects'])
    seen = set()
    expected = {'latest.json', latest['index']['url']}
    for entry in index['objects']:
        if not contract.valid_id(entry['id']) or entry['id'] in seen:
            raise DataError('Invalid or duplicate comet identity')
        seen.add(entry['id'])
        if not contract.valid_name(entry['name'], entry['id']) or entry['coverage'] != [START, START + STEP * (COUNT - 1)]:
            raise DataError('Invalid comet metadata/coverage')
        provenance = entry['provenance']
        for kind, source in provenance.items():
            if source['url'] != query(entry['id'], kind == 'midpoints') or source['api_version'] not in ('1.2', '1.3'):
                raise DataError('Invalid source query or API version')
            if source['target'] != entry['name'] or not re.fullmatch(r'JPL#[A-Za-z0-9/._-]+', source['solution']):
                raise DataError('Invalid source identity')
            if not re.fullmatch(r'[a-f0-9]{64}', source['sha256']):
                raise DataError('Invalid raw response hash')
        if provenance['vectors']['solution'] != provenance['midpoints']['solution']:
            raise DataError('Mixed Horizons solutions')
        validation = entry['validation']
        if (validation['method'] != 'all-midpoints-final-serialized' or validation['samples'] != COUNT - 1
                or validation['tolerance_au'] != TOLERANCE or not number(validation['max_error_au'])
                or not 0 <= validation['max_error_au'] <= TOLERANCE):
            raise DataError('Invalid numerical validation')
        value = read_reference(directory, entry['data'], 'objects/', MAX_OBJECT_BYTES)
        validate_object(value, entry['id'], contract=contract)
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
            'files': len(names), 'bytes': sum((directory / n).stat().st_size for n in names)}, index


def verify_comets(directory, *, contract, allow_orphans=False):
    report, _ = inspect_comets(directory, contract=contract, allow_orphans=allow_orphans)
    return report


def serialize_states(rows):
    """Project source vectors to the precision used by both public products."""
    return [[float(f'{value:.12g}') for value in row[1:]] for row in rows]


def measure_interpolation(series, middle, object_id):
    """Measure independent midpoints against final serialized states."""
    errors = [math.dist(interpolate(series, row[0]), row[1:4]) for row in middle]
    worst = max(range(len(errors)), key=errors.__getitem__)
    if errors[worst] > TOLERANCE:
        raise DataError(f'{object_id}: interpolation exceeds {TOLERANCE} AU')
    return {'method': 'all-midpoints-final-serialized', 'samples': len(errors),
            'max_error_au': errors[worst], 'worst_jd': middle[worst][0],
            'tolerance_au': TOLERANCE,
            'references': [middle[i][:4] for i in sorted({0, len(errors)-1, worst, *range(0, len(errors), 400)})]}


def build_candidate(records, stage, previous=None, *, contract, targets):
    (stage / 'objects').mkdir()
    index = {'comet_contract_version': contract.version, 'objects': []}
    for object_id, name in targets.items():
        rows, source = records[object_id + '-vectors']
        middle, check = records[object_id + '-midpoints']
        if source['solution'] != check['solution']:
            raise DataError('Horizons solution changed during acquisition; refresh both queries')
        series = {'start': START, 'step': STEP, 'states': serialize_states(rows)}
        value = {'comet_contract_version': contract.version, 'id': object_id, 'conventions': CONVENTIONS, 'series': series}
        temporary = stage / 'object.json'
        write_json(temporary, value)
        # Measure the bytes actually published, rather than the pre-rounding data.
        series = read_json(temporary)['series']
        validate_series(series)
        validation = measure_interpolation(series, middle, object_id)
        ref = reference(temporary, 'objects/', '.json')
        temporary.rename(stage / ref['url'])
        entry = {'id': object_id, 'name': name, 'coverage': [START, START + STEP * (COUNT - 1)],
                 'data': ref, 'provenance': {'vectors': source, 'midpoints': check},
                 'validation': validation}
        # Raw hashes/banner clocks identify a retrieval, not a scientific solution.
        # Reuse the prior acquisition provenance only when all scientific headers,
        # queries, serialized states and numerical results are identical.
        if previous:
            old = next((v for v in previous['objects'] if v['id'] == object_id), None)
            def scientific(item):
                return {k: v for k, v in item.items() if k not in ('sha256', 'retrieved_at')}
            if (old is not None and old['data'] == ref and old['validation'] == entry['validation']
                    and all(scientific(old['provenance'][k]) == scientific(v) for k, v in entry['provenance'].items())):
                entry['provenance'] = old['provenance']
        index['objects'].append(entry)
    temporary = stage / 'index.json'
    write_json(temporary, index)
    pin = reference(temporary, 'index-', '.json')
    temporary.rename(stage / pin['url'])
    write_json(stage / 'latest.json', {'comet_contract_version': contract.version, 'index': pin})
    return verify_comets(stage, contract=contract)


def descriptor_bytes(path):
    if not path.exists():
        return None
    with path.open('rb') as stream:
        content = stream.read(4097)
    if len(content) > 4096:
        raise DataError('Oversized comet descriptor')
    return content


def preflight_output(output):
    """Recheck inventory and atomic-rename devices at each mutation boundary."""
    names = managed_files(output)
    site = output.parent
    validate_same_filesystem(site.parent, (site, output, output / "objects",
                                         *(output / name for name in names)), label="Comet output")


def refresh_comets(cache, output, *, acquire_records, build, verify, inspect):
    cache, output = Path(cache), Path(output)
    # Check the lexical target before resolving aliases for all locks/staging.
    managed_files(output)
    output = validate_writable_path(output, label='Comet output')
    site = output.parent
    # A failed acquisition must not leave locks/raw inputs in either product.
    validate_disjoint_paths(cache, (site,), label='Horizons cache')
    preflight_output(output)
    site.mkdir(parents=True, exist_ok=True)
    lock = site_lock_path(site)
    refresh_lock = site.parent / ('.' + site.name + '-' + output.name + '-refresh-lock')
    acquisition_lock = site.parent / ('.' + site.name + '-horizons-lock')
    # Competing writers fail fast. The product lock prevents refresh overtaking;
    # the acquisition lock also excludes the other comet version across caches.
    with writer_lock(refresh_lock):
        with writer_lock(acquisition_lock):
            records = acquire_records()
        # The product lock excludes cooperating comet writers; unrelated site
        # writers preserve this namespace. Pin all previous reads to one snapshot.
        preflight_output(output)
        descriptor = output / 'latest.json'
        prior_descriptor = descriptor_bytes(descriptor)
        previous = None
        if prior_descriptor is not None:
            _, previous = inspect(output, allow_orphans=True, descriptor=prior_descriptor)
        # The output refresh lock stays held; asteroid writers and publication
        # can proceed while the new candidate is built and numerically verified.
        with tempfile.TemporaryDirectory(prefix='.comets-', dir=site.parent) as temp:
            stage = Path(temp)
            build(records, stage, previous)
            with writer_lock(lock):
                preflight_output(output)
                current_descriptor = descriptor_bytes(descriptor)
                if current_descriptor != prior_descriptor:
                    raise DataError('Comet descriptor changed during candidate preparation; retry')
                changes = sync_candidate(stage, output, inventory=managed_files)
                # Report the installed tree, independently of a builder's return.
                result = verify(output)
    return {**result, 'status': 'updated' if any(changes[k] for k in ('added', 'changed', 'deleted')) else 'unchanged',
            'changes': changes}


class CometProduct:
    """Bind one wire contract and curated acquisition policy to shared operations."""

    def __init__(self, contract, targets):
        self.contract = contract
        self.targets = targets
        # Bind policy directly; adding an operation needs no forwarding method.
        self.validate_object = partial(validate_object, contract=contract)
        self.verify_comets = partial(verify_comets, contract=contract)

    def build_candidate(self, records, stage, previous=None):
        # V2 selection can change between refreshes; v1 still enforces its fixed selection.
        return build_candidate(records, stage, previous, contract=self.contract, targets=self.targets)

    def acquire(self, cache, *, refresh=False, offline=False):
        return horizons.acquire(cache, refresh=refresh, offline=offline, targets=self.targets)

    def refresh_comets(self, cache, output, *, refresh=False, offline=False):
        return refresh_comets(cache, output,
                              acquire_records=lambda: self.acquire(cache, refresh=refresh, offline=offline),
                              build=self.build_candidate, verify=self.verify_comets,
                              inspect=partial(inspect_comets, contract=self.contract))
