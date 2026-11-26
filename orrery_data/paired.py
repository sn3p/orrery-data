"""Complete paired releases, bounded retention, and atomic authoring replacement."""
from datetime import timedelta
import hashlib
from pathlib import Path
from urllib.request import urlopen
import re
import shutil
import tempfile

from .browser import build_candidate, published_index, verify_browser, managed_files, reference, check_reference, read_verified_json
from .indexed import MAX_INDEX_BYTES
from .named_bodies import keys, need, validate_definitions
from .named_body_mapping import validate_mapping
from .storage import (read_json, write_json, json_bytes, file_info, utc_timestamp, now,
                      writer_lock, site_lock_path, rename_new, exchange_directories, validate_file_info)
from .paths import validate_writable_path, validate_disjoint_paths

BASE = 'https://sn3p.github.io/orrery-data/'
MAX_GENERATIONS = 4
GRACE_DAYS = 14
HASH = re.compile('[a-f0-9]{64}')


def inventory(directory):
    directory = Path(directory)
    if not directory.exists() and not directory.is_symlink():
        return set()
    need(directory.is_dir() and not directory.is_symlink(), 'paired directory')
    files = set()
    for p in directory.rglob('*'):
        need(not p.is_symlink(), 'paired symlink')
        need(p.is_file() or p.is_dir(), 'paired special file')
        if p.is_file():
            files.add(p.relative_to(directory).as_posix())
    expected_dirs = {parent.as_posix() for name in files for parent in Path(name).parents if parent != Path('.')}
    actual_dirs = {p.relative_to(directory).as_posix() for p in directory.rglob('*') if p.is_dir()}
    need(actual_dirs == expected_dirs, 'paired directory inventory')
    return files


def ref_schema(ref, maximum):
    keys(ref, 'url bytes sha256'); validate_file_info(ref, 'paired reference')
    need(type(ref['bytes']) is int and 0 < ref['bytes'] <= maximum, 'paired reference size')


def descriptor(value, generation):
    keys(value, 'paired_catalogue_version asteroids namedBodies')
    need(type(value['paired_catalogue_version']) is int and value['paired_catalogue_version'] == 1, 'paired version')
    for field, prefix, maximum in [('asteroids', 'asteroids/', MAX_INDEX_BYTES), ('namedBodies', 'named/', 128*1024)]:
        ref = value[field]; ref_schema(ref, maximum)
        need(ref['url'] == BASE + f'paired/generations/{generation}/' + prefix + f'index-{ref["sha256"]}.json', 'paired URL')
    expected = hashlib.sha256(json_bytes([value['asteroids']['sha256'], value['namedBodies']['sha256']])).hexdigest()
    need(generation == expected, 'paired generation identity')
    return value


def verify_generation(root, generation):
    need(bool(HASH.fullmatch(generation)), 'generation name')
    folder = root / 'generations' / generation
    need((folder/'pair.json').stat().st_size <= 4096, 'pair size')
    pair = descriptor(read_json(folder/'pair.json'), generation)
    # Browser owns only its own regular files, reserving the named subproduct.
    checked, index = verify_browser(folder/'asteroids', capture_index=True)
    need(checked['pin'] == {**pair['asteroids'], 'url': Path(pair['asteroids']['url']).name}, 'paired asteroid pin')
    named = folder/'named'; ref = {**pair['namedBodies'], 'url': Path(pair['namedBodies']['url']).name}
    product = read_verified_json(named/ref['url'], ref)
    keys(product, 'named_body_product_version definitions mapping')
    need(type(product['named_body_product_version']) is int and product['named_body_product_version'] == 1, 'product version')
    expected = {ref['url']}
    values = {}
    for key, prefix in [('definitions','definitions-'),('mapping','mapping-')]:
        ref = product[key]; ref_schema(ref, 128*1024); check_reference(ref, prefix, '.json')
        values[key] = read_verified_json(named/ref['url'], ref); expected.add(ref['url'])
    need(inventory(named) == expected, 'named inventory')
    validate_definitions(values['definitions'])
    mapping = validate_mapping(values['mapping'], index=index, index_sha256=checked['pin']['sha256'])
    for chunk in index['chunks']:
        members = [m for m in mapping['members'] if m['ordinal'] is not None and chunk['start'] <= m['ordinal'] < chunk['end']]
        if members:
            rows = read_verified_json(folder/'asteroids'/chunk['url'], chunk)
            need(all(rows[m['ordinal']-chunk['start']]['disc'] == m['discovery_jd'] for m in members), 'mapped discovery date')
    return pair, {f'generations/{generation}/asteroids/{p}' for p in managed_files(folder/'asteroids')} | {
        f'generations/{generation}/named/{p}' for p in expected} | {f'generations/{generation}/pair.json'}


def verify_paired(site):
    root = Path(site)/'paired'
    if not root.exists() and not root.is_symlink():
        return None
    names = inventory(root)
    need((root/'latest.json').stat().st_size <= 4096 and (root/'retention.json').stat().st_size <= 16384, 'paired metadata size')
    ledger = read_json(root/'retention.json'); keys(ledger, 'version generations')
    need(type(ledger['version']) is int and ledger['version'] == 1, 'retention version')
    entries = ledger['generations']
    need(isinstance(entries, list) and 1 <= len(entries) <= MAX_GENERATIONS, 'retention capacity')
    expected = {'latest.json','retention.json'}; seen = set(); current = None
    for i, entry in enumerate(entries):
        keys(entry, 'id superseded_at')
        identity = entry['id']; need(isinstance(identity,str) and identity not in seen and HASH.fullmatch(identity), 'retention identity'); seen.add(identity)
        if i == len(entries)-1:
            need(entry['superseded_at'] is None, 'current retention timestamp')
        elif entry['superseded_at'] is not None:
            utc_timestamp(entry['superseded_at'], 'superseded')
        current, files = verify_generation(root, identity); expected |= files
    need(names == expected, 'paired inventory')
    need(read_json(root/'latest.json') == current, 'paired latest')
    need(read_json(Path(site)/'latest.json')['index']['sha256'] == current['asteroids']['sha256'], 'root/paired asteroid mismatch')
    return {'descriptor': current, 'generations': len(entries), 'files': len(names),
            'bytes': sum((root/name).stat().st_size for name in names)}


def owned_files(site):
    return managed_files(site) | {'paired/'+name for name in inventory(Path(site)/'paired')}


def published_descriptor():
    try:
        from .storage import loads_json
        with urlopen(BASE+'paired/latest.json', timeout=20) as response:
            raw = response.read(4097)
        return loads_json(raw) if len(raw) <= 4096 else None
    except (OSError, ValueError):
        return None


def update(bundle, output, make_companion, *, timestamp=None, published=None):
    from .site import verify_site
    bundle, output = Path(bundle).resolve(), Path(output).resolve()
    validate_writable_path(output, label='Paired output')
    validate_disjoint_paths(output, (bundle,), label='Paired output')
    moment = utc_timestamp(timestamp or now(), 'refresh')
    output.parent.mkdir(parents=True, exist_ok=True)
    with writer_lock(site_lock_path(output)):
        old = owned_files(output)
        if old: verify_site(output)
        with tempfile.TemporaryDirectory(prefix='.paired-', dir=output.parent) as temporary:
            stage = Path(temporary)/'site'; stage.mkdir()
            build_candidate(bundle, stage, published_index(output))
            companion = make_companion(bundle, stage)
            if (output/'comets').exists(): shutil.copytree(output/'comets',stage/'comets',symlinks=True)
            root = stage/'paired'
            if (output/'paired').exists():
                shutil.copytree(output/'paired',root,symlinks=True)
                ledger = read_json(root/'retention.json')
            else:
                root.mkdir(); (root/'generations').mkdir(); ledger = {'version':1,'generations':[]}
            asteroid = read_json(stage/'latest.json')['index']
            named = reference(Path(companion['path'])/'index.json','index-','.json')
            identity = hashlib.sha256(json_bytes([asteroid['sha256'],named['sha256']])).hexdigest()
            entries = ledger['generations']
            # A grace clock starts only after observing a successfully hosted
            # replacement. Preparation/commit timestamps do not prove publication.
            confirmed = bool(entries and (published if published is not None else published_descriptor()) == read_json(root/'latest.json'))
            if confirmed:
                for entry in entries[:-1]:
                    if entry['superseded_at'] is None:
                        entry['superseded_at'] = moment.strftime('%Y-%m-%dT%H:%M:%SZ')
            if not entries or entries[-1]['id'] != identity:
                need(all(e['superseded_at'] is None or utc_timestamp(e['superseded_at'],'superseded') <= moment for e in entries), 'refresh clock moved backwards')
                # Always retain the immediately previous pair for rollback.
                removable = [e for e in entries[:-1] if confirmed and e['superseded_at'] is not None
                             and moment-utc_timestamp(e['superseded_at'],'superseded') >= timedelta(days=GRACE_DAYS)]
                for entry in removable:
                    shutil.rmtree(root/'generations'/entry['id']); entries.remove(entry)
                existing = next((e for e in entries if e['id']==identity),None)
                if existing: entries.remove(existing)
                need(len(entries) < MAX_GENERATIONS, 'retention capacity: refresh deferred to preserve 14-day pins; retry after expiry/publication')
                folder = root/'generations'/identity
                if not folder.exists():
                    folder.mkdir(); (folder/'named').mkdir(); (folder/'asteroids').mkdir()
                    for name in managed_files(stage):
                        target=folder/'asteroids'/name; target.parent.mkdir(parents=True,exist_ok=True); shutil.copyfile(stage/name,target)
                    for name in ('definitions','mapping'):
                        ref=read_json(Path(companion['path'])/'index.json')[name]
                        shutil.copyfile(Path(companion['path'])/ref['url'],folder/'named'/ref['url'])
                    shutil.copyfile(Path(companion['path'])/'index.json',folder/'named'/named['url'])
                pair = {'paired_catalogue_version':1,
                        'asteroids':{**asteroid,'url':BASE+f'paired/generations/{identity}/asteroids/'+asteroid['url']},
                        'namedBodies':{**named,'url':BASE+f'paired/generations/{identity}/named/'+named['url']}}
                write_json(folder/'pair.json',pair)
                entries.append({'id':identity,'superseded_at':None})
                write_json(root/'latest.json',pair)
            write_json(root/'retention.json',ledger)
            checked = verify_site(stage)
            new = owned_files(stage)
            changes = {'added':sorted(new-old),'deleted':sorted(old-new),'changed':[], 'unchanged':[]}
            for name in sorted(old & new):
                changes['unchanged' if file_info(output/name)==file_info(stage/name) else 'changed'].append(name)
            changed = any(changes[k] for k in ('added','changed','deleted'))
            if changed:
                if output.exists(): exchange_directories(stage,output)
                else: rename_new(stage,output)
            return {**checked,'path':str(output),'status':'updated' if changed else 'unchanged','changes':changes,
                    'named_bodies':{**companion,'activation':'paired'}}
