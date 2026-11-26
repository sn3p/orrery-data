"""Exact dwarf identity joins to a verified, immutable asteroid generation."""
from pathlib import Path
from .named_bodies import IDS, need
from .formats import FIELDS
from .records import iter_master
from .indexed import verify_source_export
from .browser import verify_browser
from .storage import read_json, verify_file


def selected_identities(master, limit):
    """Match export selection BEFORE stable date sort; do not match orbit values."""
    selected, found, seen = [], {}, set()
    for row in master:
        need(row['id'] not in seen, 'duplicate master identity')
        seen.add(row['id'])
        if row['id'] in IDS[8:]:
            found[row['id']] = row
        if row['disc'] is not None and (limit is None or len(selected) < limit):
            selected.append(row)
    selected.sort(key=lambda r: r['disc'])
    return selected, found


def build_mapping(export, browser):
    export, browser = Path(export), Path(browser)
    manifest, catalog = verify_source_export(export)
    checked = verify_browser(browser)
    index = read_json(browser/checked['pin']['url'])
    need(index['catalog_id'] == manifest['data_version'] and index['snapshot_version'] == manifest['snapshot_version']
         and index['selection'] == manifest['selection'], 'catalogue generation mismatch')
    selected, found = selected_identities(iter_master(export/'master.jsonl.gz'), manifest['selection']['limit'])
    need(len(selected) == len(catalog), 'selected row count')
    for actual, row in zip(catalog, selected):
        need(actual == {k: row[k] for k in FIELDS}, 'identity/export row mismatch')
    offset = 0
    for chunk in index['chunks']:
        verify_file(browser/chunk['url'], chunk)
        rows = read_json(browser/chunk['url'])
        need(rows == catalog[offset:offset+len(rows)], 'browser/export row mismatch')
        offset += len(rows)
    need(offset == len(catalog), 'browser coverage')
    # Detect a descriptor switch during validation; inputs are otherwise expected immutable.
    need(read_json(browser/'latest.json')['index'] == checked['pin'], 'browser changed during mapping')
    ordinals = {r['id']: n for n, r in enumerate(selected) if r['id'] in IDS[8:]}
    members = []
    for identity in IDS[8:]:
        ordinal, record = ordinals.get(identity), found.get(identity)
        reason = None if ordinal is not None else ('not-in-master' if record is None else
                 'unknown-discovery-date' if record['disc'] is None else 'selection-limit')
        members.append(dict(id=identity, status='present' if ordinal is not None else 'absent-from-export',
                            ordinal=ordinal, discovery_jd=None if record is None else record['disc'], reason=reason))
    return dict(named_body_mapping_version=1, catalog_id=index['catalog_id'], asteroid_index_sha256=checked['pin']['sha256'],
                snapshot_version=index['snapshot_version'], records=len(catalog), members=members)
