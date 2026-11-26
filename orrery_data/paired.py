"""Complete paired releases, bounded retention, and atomic authoring replacement."""
from datetime import timedelta
import hashlib
import os
from pathlib import Path
from urllib.parse import urlsplit
from .formats import DataError
import re
import shutil
import tempfile

from .browser import build_candidate, published_index, verify_browser, managed_files, reference, check_reference, read_verified_json
from .indexed import MAX_INDEX_BYTES
from .named_bodies import keys, need, validate_definitions
from .named_body_mapping import validate_mapping
from .storage import (read_json, write_json, json_bytes, utc_timestamp, now,
                      writer_lock, site_lock_path, rename_new, exchange_directories, validate_file_info)
from .paths import validate_writable_path, validate_disjoint_paths
from .publication import remote_descriptor

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


def validate_base(value):
    need(isinstance(value, str), 'publication base')
    try:
        url = urlsplit(value)
        url.port  # Validate malformed/out-of-range ports before generating references.
    except ValueError as exc:
        raise DataError(f'Invalid publication base: {exc}') from exc
    need(url.scheme == 'https' and bool(url.hostname) and not url.username and not url.password
         and not url.query and not url.fragment, 'publication base requires HTTPS without credentials/query/fragment')
    return value.rstrip('/')+'/'


def descriptor(value, generation):
    keys(value, 'paired_catalogue_version asteroids namedBodies')
    need(type(value['paired_catalogue_version']) is int and value['paired_catalogue_version'] == 1, 'paired version')
    bases = set()
    for field, maximum in [('asteroids', MAX_INDEX_BYTES), ('namedBodies', 128*1024)]:
        ref = value[field]; ref_schema(ref, maximum)
        suffix = (f'paired/asteroids/{ref["sha256"]}/' if field == 'asteroids'
                  else f'paired/generations/{generation}/named/') + f'index-{ref["sha256"]}.json'
        need(isinstance(ref['url'], str) and ref['url'].endswith(suffix), 'paired URL')
        base = ref['url'][:-len(suffix)]
        need(validate_base(base) == base, 'paired base'); bases.add(base)
    need(len(bases) == 1, 'paired base mismatch')
    expected = hashlib.sha256(json_bytes([value['asteroids']['sha256'], value['namedBodies']['sha256']])).hexdigest()
    need(generation == expected, 'paired generation identity')
    return value


def file_state(path):
    stat = path.lstat()
    # Hard-link creation changes ctime/link count, but never file contents.
    return (stat.st_dev, stat.st_ino, stat.st_mode, stat.st_size, stat.st_mtime_ns)


def tree_state(directory):
    """Optimistic concurrency token, captured/rechecked under the writer lock."""
    if not directory.exists():
        return None
    return {p.relative_to(directory).as_posix(): file_state(p)
            for p in [directory, *directory.rglob('*')]}


def link_snapshot_file(source, destination):
    """Private mutable metadata; immutable payloads share only their inode."""
    if Path(source).name in ('latest.json', 'retention.json', 'pair.json'):
        return shutil.copyfile(source, destination)
    os.link(source, destination)
    return destination


def verify_generation(root, generation, catalogues):
    need(bool(HASH.fullmatch(generation)), 'generation name')
    folder = root / 'generations' / generation
    need((folder/'pair.json').stat().st_size <= 4096, 'pair size')
    pair = descriptor(read_json(folder/'pair.json'), generation)
    # Browser owns only its own regular files, reserving the named subproduct.
    asteroid_hash = pair['asteroids']['sha256']
    asteroid_directory = root/'asteroids'/asteroid_hash
    names = managed_files(asteroid_directory)
    state = {p: file_state(asteroid_directory/p) for p in names}
    cached = catalogues.get(asteroid_hash)
    if cached is not None and cached[0] == state:
        _, checked, index = cached
    else:
        checked, index = verify_browser(asteroid_directory, capture_index=True)
        catalogues[asteroid_hash] = state, checked, index
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
            rows = read_verified_json(asteroid_directory/chunk['url'], chunk)
            need(all(rows[m['ordinal']-chunk['start']]['disc'] == m['discovery_jd'] for m in members), 'mapped discovery date')
    return pair, {f'asteroids/{asteroid_hash}/{p}' for p in names} | {
        f'generations/{generation}/named/{p}' for p in expected} | {f'generations/{generation}/pair.json'}


def verify_paired(site, *, catalogues=None):
    try:
        return _verify_paired(site, {} if catalogues is None else catalogues)
    except OSError as exc:
        raise DataError(f'Unable to read paired publication: {exc}') from exc


def _verify_paired(site, catalogues):
    root = Path(site)/'paired'
    if not root.exists() and not root.is_symlink():
        return None
    names = inventory(root)
    need((root/'latest.json').stat().st_size <= 4096 and (root/'retention.json').stat().st_size <= 4096, 'paired metadata size')
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
        current, files = verify_generation(root, identity, catalogues); expected |= files
    need(names == expected, 'paired inventory')
    need(read_json(root/'latest.json') == current, 'paired latest')
    # Root latest is the independently refreshed legacy product. A retained pair
    # remains internally exact even when its replacement must be deferred.
    return {'descriptor': current, 'generations': len(entries), 'files': len(names),
            'bytes': sum((root/name).stat().st_size for name in names)}


def owned_files(site):
    return managed_files(site) | {'paired/'+name for name in inventory(Path(site)/'paired')}


def published_descriptor(base_url=BASE):
    try:
        return remote_descriptor(validate_base(base_url)+'paired/latest.json', optional=True)
    except (OSError, ValueError):
        return None


def update(bundle, output, make_companion, *, timestamp=None, published=None, base_url=BASE):
    from .site import verify_asteroids, enforce_byte_budget
    bundle, output = Path(bundle).resolve(), Path(output).resolve()
    validate_writable_path(output, label='Paired output')
    validate_disjoint_paths(output, (bundle,), label='Paired output')
    if base_url is not None:
        base_url = validate_base(base_url)
    moment = utc_timestamp(timestamp or now(), 'refresh')
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.paired-', dir=output.parent) as temporary:
        snapshot, stage = Path(temporary)/'snapshot', Path(temporary)/'site'
        # All cooperating writers replace files, never modify linked payloads in
        # place. Snapshotting and the final exchange are the only locked phases.
        with writer_lock(site_lock_path(output)):
            old = owned_files(output)
            if output.exists():
                shutil.copytree(output, snapshot, symlinks=True, copy_function=link_snapshot_file)
            else:
                snapshot.mkdir()
            expected_state = tree_state(output)
        catalogues = {}
        if old:
            verify_asteroids(snapshot, catalogues=catalogues)
        stage.mkdir()
        build_candidate(bundle, stage, published_index(snapshot))
        companion = make_companion(bundle, stage)
        if (snapshot/'comets').exists():
            (snapshot/'comets').rename(stage/'comets')
        root = stage/'paired'
        if (snapshot/'paired').exists():
            (snapshot/'paired').rename(root)
            ledger = read_json(root/'retention.json')
        else:
            ledger = {'version':1, 'generations':[]}
        asteroid = read_json(stage/'latest.json')['index']
        deferred = None
        named = reference(Path(companion['path'])/'index.json', 'index-', '.json')
        companion_index = read_verified_json(Path(companion['path'])/'index.json', named)
        identity = hashlib.sha256(json_bytes([asteroid['sha256'], named['sha256']])).hexdigest()
        entries = ledger['generations']
        if base_url is None:
            deferred = 'Pages configuration unavailable; retained pair unchanged'
        else:
            if not root.exists():
                (root/'generations').mkdir(parents=True)
                (root/'asteroids').mkdir()
            current = read_json(root/'latest.json') if entries else None
            migrating = bool(current and not current['namedBodies']['url'].startswith(base_url+'paired/generations/'))
            if migrating:
                # Old-host publication evidence cannot expire newly hosted URLs.
                for entry in entries:
                    entry['superseded_at'] = None
            confirmed = bool(entries and not migrating and
                             (published if published is not None else published_descriptor(base_url)) == current)
            if confirmed:
                for entry in entries[:-1]:
                    if entry['superseded_at'] is None:
                        entry['superseded_at'] = moment.strftime('%Y-%m-%dT%H:%M:%SZ')
            if not entries or entries[-1]['id'] != identity:
                need(all(e['superseded_at'] is None or utc_timestamp(e['superseded_at'], 'superseded') <= moment
                         for e in entries), 'refresh clock moved backwards')
                removable = [e for e in entries[:-1] if confirmed and e['superseded_at'] is not None
                             and moment-utc_timestamp(e['superseded_at'], 'superseded') >= timedelta(days=GRACE_DAYS)]
                for entry in removable:
                    shutil.rmtree(root/'generations'/entry['id'])
                    entries.remove(entry)
                existing = next((e for e in entries if e['id'] == identity), None)
                if existing:
                    entries.remove(existing)
                if len(entries) >= MAX_GENERATIONS:
                    # Keep the independently prepared root and commit confirmation
                    # evidence even when a new pair cannot fit its support window.
                    deferred = 'retention capacity: retry after hosted confirmation and 14-day expiry'
                else:
                    asteroid_directory = root/'asteroids'/asteroid['sha256']
                    if not asteroid_directory.exists():
                        asteroid_directory.mkdir()
                        for name in managed_files(stage):
                            target = asteroid_directory/name
                            target.parent.mkdir(parents=True, exist_ok=True)
                            link_snapshot_file(stage/name, target)
                    folder = root/'generations'/identity
                    if not folder.exists():
                        (folder/'named').mkdir(parents=True)
                        for name in ('definitions', 'mapping'):
                            ref = companion_index[name]
                            shutil.copyfile(Path(companion['path'])/ref['url'], folder/'named'/ref['url'])
                        shutil.copyfile(Path(companion['path'])/'index.json', folder/'named'/named['url'])
                        write_json(folder/'pair.json', {
                            'paired_catalogue_version':1,
                            'asteroids':{**asteroid, 'url':base_url+f'paired/asteroids/{asteroid["sha256"]}/'+asteroid['url']},
                            'namedBodies':{**named, 'url':base_url+f'paired/generations/{identity}/named/'+named['url']}})
                    entries.append({'id':identity, 'superseded_at':None})
            write_json(root/'retention.json', ledger)
            used_catalogues = set()
            for entry in entries:
                path = root/'generations'/entry['id']/'pair.json'
                pair = read_json(path)
                asteroid_hash = pair['asteroids']['sha256']
                used_catalogues.add(asteroid_hash)
                pair['asteroids']['url'] = base_url+f'paired/asteroids/{asteroid_hash}/index-{asteroid_hash}.json'
                named_hash = pair['namedBodies']['sha256']
                pair['namedBodies']['url'] = base_url+f"paired/generations/{entry['id']}/named/index-{named_hash}.json"
                write_json(path, pair)
            write_json(root/'latest.json', pair)
            # Shared asteroid payloads live as long as any supported pair uses them.
            for folder in (root/'asteroids').iterdir():
                if folder.name not in used_catalogues:
                    shutil.rmtree(folder)
        checked = verify_asteroids(stage, catalogues=catalogues)
        # Account for the preserved namespace without making its scientific
        # validation a prerequisite for authoring asteroid data.
        comet_bytes = sum(p.lstat().st_size for p in (stage/'comets').rglob('*')
                          if p.is_file() and not p.is_symlink())
        enforce_byte_budget(checked['bytes'] + comet_bytes)
        new = owned_files(stage)
        changes = {'added':sorted(new-old), 'deleted':sorted(old-new), 'changed':[], 'unchanged':[]}
        with writer_lock(site_lock_path(output)):
            need(tree_state(output) == expected_state, 'site changed during refresh; retry')
            for name in sorted(old & new):
                # Both snapshots were verified. Equal immutable hash paths need
                # no further digest pass; mutable metadata is compared directly.
                immutable = re.fullmatch(r'(?:index-|definitions-|mapping-|header-|notice-)?[a-f0-9]{64}\.(?:json|txt)', Path(name).name)
                equal = bool(immutable) or (output/name).read_bytes() == (stage/name).read_bytes()
                changes['unchanged' if equal else 'changed'].append(name)
            changed = any(changes[k] for k in ('added', 'changed', 'deleted'))
            if changed:
                if output.exists():
                    exchange_directories(stage, output)
                else:
                    rename_new(stage, output)
        return {**checked, 'path':str(output), 'status':'updated' if changed else 'unchanged', 'changes':changes,
                'deferred':deferred,
                'named_bodies':{**companion, 'activation':'deferred' if deferred else 'paired'}}
