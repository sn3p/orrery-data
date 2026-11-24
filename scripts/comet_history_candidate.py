#!/usr/bin/env python3
"""Build/verify unpublished schema-3 candidates from retained, validated inputs.

No acquisition or installation mode. Verification replays scientific checks from
raw responses, rather than trusting a candidate's self-reported evidence.
"""
import argparse
import hashlib
import math
import re
from datetime import date
from urllib.parse import urlparse
from pathlib import Path
import sys
import stat
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts import comet_history_pilot as pilot
from orrery_data.comet_product import CometContract, inspect_comets, interpolate_state
from orrery_data.formats import DataError
from orrery_data.paths import validate_disjoint_paths, path_within
from orrery_data.storage import encode, loads_json, writer_lock, utc_timestamp

T = 2444239.5
END = 2488067.5
LIMIT = 2 * 1024 * 1024
IDS = ['1P', '2P', '55P', '9P', '10P', '67P']


def json_bytes(value):
    return (encode(value) + '\n').encode()


def reference(raw, prefix):
    digest = hashlib.sha256(raw).hexdigest()
    return dict(url=f'{prefix}{digest}.json', sha256=digest, bytes=len(raw))


def interval(start, end, inclusive=True):
    return dict(start=start, end=end, end_inclusive=inclusive)


def validate_metadata(value):
    """Complete public metadata validation, independent of source acquisition."""
    def fields(obj, required, optional=()):
        if not isinstance(obj, dict) or not set(required) <= obj.keys() or not obj.keys() <= set(required) | set(optional):
            raise DataError('Invalid public metadata fields')
    def require(ok):
        if not ok:
            raise DataError('Invalid public metadata')
    def text(v):
        return isinstance(v, str) and bool(v)
    def nullable(v):
        return v is None or isinstance(v, str)
    def day(v):
        require(isinstance(v, str) and bool(re.fullmatch(r'\d{4}-\d{2}-\d{2}', v)))
        try:
            date.fromisoformat(v)
        except ValueError as exc:
            raise DataError('Invalid public metadata date') from exc
    fields(value, ['experimental', 'status', 'sources', 'objects'])
    require(value['experimental'] is True and text(value['status']))
    require(isinstance(value['sources'], dict))
    for source in value['sources'].values():
        fields(source, ['url', 'checked_on'], ['note', 'description'])
        require(text(source['url']))
        url = urlparse(source['url'])
        require(url.scheme == 'https' and url.hostname and not url.username and not url.password)
        day(source['checked_on'])
        require(all(text(source[k]) for k in ('note', 'description') if k in source))
    require(isinstance(value['objects'], list) and len(value['objects']) == len(IDS)
            and all(isinstance(o, dict) for o in value['objects']) and [o.get('id') for o in value['objects']] == IDS)
    for obj in value['objects']:
        fields(obj, ['id', 'catalogue_discovery', 'catalogue_provenance', 'events', 'event_inventory_complete'])
        require(obj['event_inventory_complete'] is False)
        fields(obj['catalogue_discovery'], ['citation','name','who','location','site','discovery','cref','date','ref'])
        require(all(nullable(v) for v in obj['catalogue_discovery'].values()))
        p = obj['catalogue_provenance']
        fields(p, ['url','raw_file','sha256','retrieved_on','retrieved_at','note'])
        require(p['raw_file'] == f"sources/{obj['id']}-sbdb.json" and p['url'] == f"https://ssd-api.jpl.nasa.gov/sbdb.api?sstr={obj['id']}&discovery=true&full-prec=true"
                and isinstance(p['sha256'], str) and bool(re.fullmatch(r'[a-f0-9]{64}', p['sha256'])) and nullable(p['note']))
        day(p['retrieved_on'])
        if p['retrieved_at'] is not None:
            utc_timestamp(p['retrieved_at'], 'Discovery')
            require(p['retrieved_at'].startswith(p['retrieved_on']))
        require(isinstance(obj['events'], list) and bool(obj['events']))
        for event in obj['events']:
            fields(event, ['kind','date','people','sources','certainty','note'])
            require(event['certainty'] in ('documented', 'day-disputed') and nullable(event['note'])
                and isinstance(event['people'], list) and all(text(v) for v in event['people'])
                and isinstance(event['sources'], list) and bool(event['sources'])
                and all(text(v) and v in value['sources'] for v in event['sources']))
            fields(event['date'], ['year','month','day','precision','calendar','year_numbering'])
            d = event['date']
            require(event['kind'] in ('discovery', 'independent_discovery', 'recovery', 'earlier_sighting',
                                      'photographic_observation', 'discovery_recognition'))
            require(type(d['year']) is int and abs(d['year']) <= 9999
                    and d['year_numbering'] == 'astronomical'
                    and d['calendar'] in ('gregorian', 'historical-unspecified')
                    and d['precision'] in ('year', 'month', 'day'))
            if d['precision'] == 'year':
                require(d['month'] is None and d['day'] is None)
            else:
                require(type(d['month']) is int and 1 <= d['month'] <= 12)
                if d['precision'] == 'month':
                    require(d['day'] is None)
                else:
                    require(type(d['day']) is int and d['calendar'] == 'gregorian' and d['year'] > 0)
                    day(f"{d['year']:04d}-{d['month']:02d}-{d['day']:02d}")


def construct(modern=ROOT / 'data/comets'):
    """Revalidate raw model identity and all serialized historical intervals."""
    _, baseline = inspect_comets(modern, contract=CometContract(2))
    if [e['id'] for e in baseline['objects']] != IDS:
        raise DataError('Historical candidate requires the six-object modern catalogue')
    metadata = pilot.validate_metadata(ROOT / 'research/comet-history')
    validate_metadata(metadata)
    files, entries = {}, []
    for entry, discovery in zip(baseline['objects'], metadata['objects']):
        ident = entry['id']
        raw = (modern / entry['data']['url']).read_bytes()
        files[entry['data']['url']] = raw
        modern_series = loads_json(raw)['series']
        segments = [dict(key='modern', payload_contract_version=2,
            data=entry['data'], sample_coverage=entry['coverage'],
            coverage=interval(T, END), provenance=entry['provenance'], validation=entry['validation'])]
        transition = None
        if ident in pilot.CASES:
            name, record, solution, day = pilot.CASES[ident]
            if entry['name'] != name or any(p['solution'] != solution for p in entry['provenance'].values()):
                raise DataError('Historical target or modern solution changed; reassess the transition')
            start = pilot.jd(day)
            full_count = int((END - start) // 4) + 1
            count = math.ceil((T - start) / 4) + 1
            url = pilot.query(record, start, full_count)
            source_raw, info, (rows, header) = pilot.acquire(url, False,
                lambda body: pilot.parse(body, name, solution, start, full_count))
            series = dict(start=start, step=4, states=pilot.serialize_states(rows[:count]))
            value = dict(comet_contract_version=3, id=ident, segment='historical',
                         conventions=pilot.CONVENTIONS, series=series)
            raw = json_bytes(value)
            if len(raw) > LIMIT:
                raise DataError('Historical object exceeds byte budget')
            series = loads_json(raw)['series']
            provenance = {'vectors': dict(info, header=header, target=name, solution=solution, api_version=loads_json(source_raw)['signature']['version'])}
            checks = []
            for offset in (1, 2, 3):
                def validate(body):
                    refs, ref_header = pilot.parse(body, name, solution, start + offset, full_count - 1)
                    if pilot.model_identity(header) != pilot.model_identity(ref_header):
                        raise DataError('Scientific model changed between requests')
                    return refs, ref_header
                source_raw, info, (refs, ref_header) = pilot.acquire(pilot.query(record, start, full_count, offset), False, validate)
                refs = refs[:count - 1]
                errors = [math.dist(pilot.interpolate(series, r[0]), r[1:4]) for r in refs]
                worst = max(range(len(errors)), key=errors.__getitem__)
                if errors[worst] > pilot.TOLERANCE:
                    raise DataError('Historical serialized interpolation exceeds tolerance')
                checks.append(dict(offset_days=offset, samples=len(refs), max_error_au=errors[worst],
                    worst_jd=refs[worst][0], references=[refs[i][:4] for i in sorted({0, worst, len(refs)-1})]))
                provenance[str(offset)] = dict(info, header=ref_header, target=name, solution=solution, api_version=loads_json(source_raw)['signature']['version'])
            # Check both components at the ownership boundary and at the support
            # overlap endpoint (55P January 2). Never rewrite either state series.
            overlap_end = start + (count - 1) * 4
            comparisons = []
            for time in sorted({T, overlap_end}):
                hp, hv = interpolate_state(series, time)
                mp, mv = interpolate_state(modern_series, time)
                dp, dv = math.dist(hp, mp), math.dist(hv, mv)
                if dp > pilot.TOLERANCE or dv > pilot.BASELINE_VELOCITY_LIMIT:
                    raise DataError('Historical transition exceeds position/velocity limits')
                comparisons.append(dict(jd=time, position_difference_au=dp, velocity_difference_au_per_day=dv))
            transition = dict(jd=T, owner='modern', position_limit_au=pilot.TOLERANCE,
                velocity_limit_au_per_day=pilot.BASELINE_VELOCITY_LIMIT, comparisons=comparisons)
            ref = reference(raw, 'objects/')
            files[ref['url']] = raw
            segments.insert(0, dict(key='historical', payload_contract_version=3, data=ref,
                sample_coverage=[start, overlap_end], coverage=interval(start, T, False),
                provenance=provenance, validation=dict(method='all-quarter-points-final-serialized',
                    tolerance_au=pilot.TOLERANCE, checks=checks)))
        entries.append(dict(id=ident, name=entry['name'], discovery=discovery,
                            segments=segments, transition=transition))
    index = dict(comet_contract_version=3, sources=metadata['sources'], objects=entries)
    raw = json_bytes(index)
    if len(raw) > 256 * 1024:
        raise DataError('Historical index exceeds byte budget')
    ref = reference(raw, 'index-')
    files[ref['url']] = raw
    files['latest.json'] = json_bytes(dict(comet_contract_version=3, index=ref))
    return files


def verify(directory, expected):
    """Exact replay verifies metadata/provenance too, including unknown fields."""
    directory = Path(directory)
    if directory.is_symlink() or not directory.is_dir():
        raise DataError('Candidate must be a real directory')
    names = set()
    for path in directory.rglob('*'):
        name = path.relative_to(directory).as_posix()
        mode = path.lstat().st_mode
        if not stat.S_ISREG(mode) and not (stat.S_ISDIR(mode) and name == 'objects'):
            raise DataError('Unexpected candidate inventory')
        if path.is_file():
            names.add(name)
            if name not in expected or path.stat().st_size != len(expected[name]) or path.read_bytes() != expected[name]:
                raise DataError('Candidate differs from validated inputs: ' + name)
    if names != expected.keys():
        raise DataError('Candidate inventory mismatch')
    return dict(files=len(names), bytes=sum(map(len, expected.values())),
                pin=loads_json(expected['latest.json'])['index'])


def build(output, *, verify_only=False):
    output = Path(output)
    # Explicitly restrict this research command to ignored workspace candidates.
    # Resolve aliases before any cache lock, replay, directory creation or write.
    if not path_within(output, ROOT / '.context/comet-candidates'):
        raise DataError('Output must be under .context/comet-candidates')
    output = validate_disjoint_paths(output, [ROOT / 'data', pilot.OUTPUT, ROOT / 'research'], label='Candidate')
    if output == (ROOT / '.context/comet-candidates').resolve():
        raise DataError('Choose a named candidate directory')
    expected = construct()
    if verify_only or output.exists():
        return verify(output, expected)
    output.parent.mkdir(parents=True, exist_ok=True)
    with writer_lock(output.parent):
        if output.exists():
            return verify(output, expected)
        with tempfile.TemporaryDirectory(prefix='.history-', dir=output.parent) as temporary:
            staged = Path(temporary) / 'candidate'
            for name, raw in expected.items():
                path = staged / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(raw)
            report = verify(staged, expected)
            staged.rename(output)
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / '.context/comet-candidates/history-v3')
    parser.add_argument('--verify', action='store_true', help='Replay raw evidence and verify an existing candidate')
    args = parser.parse_args()
    try:
        print(encode(build(args.output, verify_only=args.verify)))
    except (DataError, OSError) as exc:
        parser.exit(1, f'{exc}\n')
