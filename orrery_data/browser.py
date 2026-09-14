"""One complete current browser distribution, separate from indexed bundle v1."""

from pathlib import Path
import re
import shutil
import stat
import tempfile

from .contracts import validate_contract, obj, literal, TEXT, FILE_FIELDS
from .formats import DataError
from .indexed import verify_indexed, MAX_INDEX_BYTES, MAX_CHUNK_BYTES
from .paths import validate_disjoint_paths, validate_writable_path
from .pipeline import validate_snapshot_counts
from .records import validate_catalog_record
from .storage import read_json, write_json, file_info, verify_file, writer_lock

LATEST = obj({'browser_contract_version': literal(1), 'index': obj({'url': TEXT, **FILE_FIELDS})})
MANAGED = re.compile(r'(?:latest\.json|index-[a-f0-9]{64}\.json|(?:header|notice)-[a-f0-9]{64}\.txt|chunks/[a-f0-9]{64}\.json)')


def reference(path, prefix, suffix):
    info = file_info(path)
    return {'url': f'{prefix}{info["sha256"]}{suffix}', **info}


def check_reference(ref, prefix, suffix):
    if ref['url'] != f'{prefix}{ref["sha256"]}{suffix}':
        raise DataError('Browser reference must use its content hash in the path')


def managed_files(directory):
    """Refuse unrelated trees; interrupted updates may leave managed orphans."""
    if not directory.exists():
        if directory.is_symlink():
            raise DataError('Browser output cannot be a symlink')
        return set()
    if not stat.S_ISDIR(directory.lstat().st_mode):
        raise DataError('Browser output must be a real directory')
    names = set()
    for path in directory.iterdir():
        if path.name == 'chunks' and stat.S_ISDIR(path.lstat().st_mode):
            entries = list(path.iterdir())
        else:
            entries = [path]
        for item in entries:
            name = item.relative_to(directory).as_posix()
            if not stat.S_ISREG(item.lstat().st_mode) or not MANAGED.fullmatch(name):
                raise DataError(f'Unexpected browser output entry: {name}')
            names.add(name)
    return names


def verify_browser(directory, index_sha256=None):
    directory = Path(directory)
    names = managed_files(directory)
    if 'latest.json' not in names or (directory / 'latest.json').stat().st_size > 4096:
        raise DataError('Missing or oversized latest.json')
    latest = read_json(directory / 'latest.json')
    LATEST(latest, 'latest')
    pin = latest['index']
    check_reference(pin, 'index-', '.json')
    if pin['bytes'] < 1 or pin['bytes'] > MAX_INDEX_BYTES:
        raise DataError('Invalid browser index size')
    if index_sha256 is not None and index_sha256 != pin['sha256']:
        raise DataError('Browser index pin mismatch')
    if pin['url'] not in names:
        raise DataError('Missing browser index')
    verify_file(directory / pin['url'], pin)
    index = read_json(directory / pin['url'])
    validate_contract('browser', index)
    counts, selection = index['counts'], index['selection']
    validate_snapshot_counts({key: value for key, value in counts.items() if key != 'discovery_export'})
    expected = min(selection['limit'], counts['known_discovery']) if selection['limit'] else counts['known_discovery']
    if (counts['discovery_export'] != expected or index['exclusions'] != {
            'master': {'non_elliptic_orbits': counts['unsupported_orbits']},
            'discovery': {'missing_discovery_date': counts['missing_discovery']},
            'selection_limit': counts['known_discovery'] - expected}):
        raise DataError('Browser selection/count mismatch')
    if not 1 <= index['chunk_bytes'] <= MAX_CHUNK_BYTES:
        raise DataError('Invalid browser chunk cap')
    expected_names = {'latest.json', pin['url']}
    for key, ref in index['provenance'].items():
        check_reference(ref, key + '-', '.txt')
        expected_names.add(ref['url'])
    for chunk in index['chunks']:
        check_reference(chunk, 'chunks/', '.json')
        if not 1 <= chunk['bytes'] <= index['chunk_bytes']:
            raise DataError('Invalid browser chunk size')
        expected_names.add(chunk['url'])
    if names != expected_names:
        raise DataError('Browser inventory differs from latest index')
    for ref in index['provenance'].values():
        verify_file(directory / ref['url'], ref)
        (directory / ref['url']).read_text(encoding='utf-8')
    dates, ordinal = [], 0
    for chunk in index['chunks']:
        verify_file(directory / chunk['url'], chunk)
        rows = read_json(directory / chunk['url'])
        if (not isinstance(rows, list) or not rows or chunk['start'] != ordinal
                or chunk['end'] != ordinal + len(rows)):
            raise DataError('Browser ordinal coverage mismatch')
        for row in rows:
            validate_catalog_record(row)
            ordinal += 1
            if dates and row['disc'] < dates[-1][0]:
                raise DataError('Browser discovery order mismatch')
            if dates and row['disc'] == dates[-1][0]:
                dates[-1][1] = ordinal
            else:
                dates.append([row['disc'], ordinal])
        if chunk['first_disc'] != rows[0]['disc'] or chunk['last_disc'] != rows[-1]['disc']:
            raise DataError('Browser chunk dates mismatch')
    if ordinal != expected or dates != index['date_counts']:
        raise DataError('Browser date/count coverage mismatch')
    return {'path': str(directory), 'pin': pin, 'records': ordinal, 'chunks': len(index['chunks']),
            'files': len(names), 'bytes': sum((directory / name).stat().st_size for name in names)}


def build_candidate(bundle, stage):
    # Full source verification precedes projection. This does not prune/relax v1.
    verify_indexed(bundle)
    original = read_json(bundle / 'index.json')
    index = {key: value for key, value in original.items()
             if key not in ('contract_version', 'full', 'chunks', 'provenance')}
    index.update(browser_contract_version=1, chunks=[], provenance={})
    for key in ('header', 'notice'):
        source = bundle / original['provenance'][key]['url']
        ref = reference(source, key + '-', '.txt')
        shutil.copyfile(source, stage / ref['url'])
        index['provenance'][key] = ref
    if original['chunks']:
        (stage / 'chunks').mkdir()
    for chunk in original['chunks']:
        ref = {key: value for key, value in chunk.items() if key != 'gzip'}
        ref['url'] = 'chunks/' + chunk['sha256'] + '.json'
        shutil.copyfile(bundle / chunk['url'], stage / ref['url'])
        index['chunks'].append(ref)
    temporary = stage / 'index.json'
    write_json(temporary, index)
    pin = reference(temporary, 'index-', '.json')
    temporary.rename(stage / pin['url'])
    write_json(stage / 'latest.json', {'browser_contract_version': 1, 'index': pin})
    return verify_browser(stage)


def sync_candidate(stage, output):
    """Authoring-tree update: immutable files first, pointer last, then prune.

    A crash may leave extra managed files; rerun to finish. Deployment always
    verifies a complete committed tree, never a live authoring directory.
    """
    old, new = managed_files(output), managed_files(stage)
    added, changed, unchanged = [], [], []
    for name in sorted(new):
        if name not in old:
            added.append(name)
        elif file_info(stage / name) == file_info(output / name):
            unchanged.append(name)
        else:
            changed.append(name)
    output.mkdir(parents=True, exist_ok=True)
    writes = sorted(set(added + changed) - {'latest.json'})
    if 'latest.json' in added + changed:
        writes.append('latest.json')
    for name in writes:
        target = output / name
        target.parent.mkdir(exist_ok=True)
        # Stage is on the same filesystem and has already passed verification.
        (stage / name).replace(target)
    deleted = sorted(old - new)
    for name in deleted:
        (output / name).unlink()
    if (output / 'chunks').exists() and not any((output / 'chunks').iterdir()):
        (output / 'chunks').rmdir()
    return {'added': added, 'changed': changed, 'unchanged': unchanged, 'deleted': deleted}


def export_browser(bundle, output):
    bundle, output = Path(bundle), Path(output)
    validate_writable_path(output, label='Browser output')
    validate_disjoint_paths(output, (bundle,), label='Browser output')
    managed_files(output)
    lock = output.parent / ('.' + output.name + '-browser-lock')
    with writer_lock(lock):
        with tempfile.TemporaryDirectory(prefix='.browser-', dir=output.parent) as temp:
            stage = Path(temp)
            build_candidate(bundle, stage)
            changes = sync_candidate(stage, output)
            result = verify_browser(output)
    return {**result, 'status': 'updated' if any(changes[k] for k in ('added', 'changed', 'deleted')) else 'unchanged',
            'changes': changes}
