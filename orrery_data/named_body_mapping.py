"""Exact dwarf identity joins to a verified, immutable asteroid generation."""
from pathlib import Path
import re
from .named_bodies import IDS, need, keys, finite
from .indexed import verify_source_export
from .browser import verify_browser


def validate_mapping(value, *, index=None, index_sha256=None):
    keys(value, 'named_body_mapping_version catalog_id asteroid_index_sha256 snapshot_version records members')
    need(type(value['named_body_mapping_version']) is int and value['named_body_mapping_version'] == 1, 'mapping version')
    for key, prefix in [('catalog_id', 'export-v1-'), ('snapshot_version', 'snapshot-v1-'), ('asteroid_index_sha256', '')]:
        need(isinstance(value[key], str) and re.fullmatch(prefix+'[a-f0-9]{64}', value[key]), key)
    need(type(value['records']) is int and value['records'] >= 0, 'mapping count')
    if index is not None:
        need(value['catalog_id'] == index['catalog_id'] and value['snapshot_version'] == index['snapshot_version']
             and value['records'] == index['counts']['discovery_export'] and value['asteroid_index_sha256'] == index_sha256, 'mapping generation')
    need(isinstance(value['members'], list) and len(value['members']) == 5, 'mapping inventory')
    used = set()
    for identity, member in zip(IDS[8:], value['members']):
        keys(member, 'id status ordinal discovery_jd reason')
        need(member['id'] == identity and (member['discovery_jd'] is None or finite(member['discovery_jd'])), 'mapping identity/date')
        ordinal = member['ordinal']
        if member['status'] == 'present':
            need(type(ordinal) is int and 0 <= ordinal < value['records'] and ordinal not in used
                 and member['discovery_jd'] is not None and member['reason'] is None, 'present mapping')
            used.add(ordinal)
        else:
            need(member['status'] == 'absent-from-export' and ordinal is None
                 and member['reason'] in ('not-in-master', 'unknown-discovery-date', 'selection-limit'), 'absence')
            need((member['reason'] == 'selection-limit') == (member['discovery_jd'] is not None), 'absence date')
    return value


def build_mapping(export, browser):
    # Preserve the selected identities from the SAME master pass that verifies
    # the export. Browser verification compares its captured rows to that export.
    manifest, catalog, found = verify_source_export(Path(export), identity_ids=set(IDS[8:]))
    checked, index = verify_browser(Path(browser), expected_rows=catalog, capture_index=True)
    need(index['catalog_id'] == manifest['data_version'] and index['snapshot_version'] == manifest['snapshot_version']
         and index['selection'] == manifest['selection'], 'catalogue generation mismatch')
    members = []
    for identity in IDS[8:]:
        record = found.get(identity)
        ordinal = None if record is None else record['ordinal']
        reason = None if ordinal is not None else ('not-in-master' if record is None else
                 'unknown-discovery-date' if record['discovery_jd'] is None else 'selection-limit')
        members.append(dict(id=identity, status='present' if ordinal is not None else 'absent-from-export',
                            ordinal=ordinal, discovery_jd=None if record is None else record['discovery_jd'], reason=reason))
    value = dict(named_body_mapping_version=1, catalog_id=index['catalog_id'], asteroid_index_sha256=checked['pin']['sha256'],
                 snapshot_version=index['snapshot_version'], records=len(catalog), members=members)
    return validate_mapping(value, index=index, index_sha256=checked['pin']['sha256'])
