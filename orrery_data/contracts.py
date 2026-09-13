"""Complete schema-1 manifest shapes, independent of producer constructors.

These checks establish types and allowed values. Readers additionally verify
identity hashes, repeated metadata, payload relationships and trusted pins.
"""

import re
from urllib.parse import urlsplit

from .formats import DataError
from .metadata import timestamp
from .storage import utc_timestamp


def rule(predicate, description):
    def check(value, path):
        if not predicate(value):
            raise DataError(f"{path}: expected {description}")
    return check


def obj(fields, optional=()):
    def check(value, path):
        if (not isinstance(value, dict) or not set(fields) - set(optional) <= value.keys()
                or not value.keys() <= fields.keys()):
            raise DataError(f"{path}: invalid fields; required {', '.join(sorted(set(fields) - set(optional)))}")
        for key in value:
            fields[key](value[key], f"{path}.{key}")
    return check


def array(item):
    def check(value, path):
        if not isinstance(value, list):
            raise DataError(f"{path}: expected array")
        for index, entry in enumerate(value):
            item(entry, f"{path}[{index}]")
    return check


def mapping(item):
    def check(value, path):
        if not isinstance(value, dict):
            raise DataError(f"{path}: expected object")
        for key, entry in value.items():
            item(entry, f"{path}.{key}")
    return check


def nullable(check):
    return lambda value, path: None if value is None else check(value, path)


def literal(value):
    return rule(lambda candidate: type(candidate) is type(value) and candidate == value, repr(value))


def pattern(expression):
    return rule(lambda value: isinstance(value, str) and re.fullmatch(expression, value) is not None, expression)


def http_url(value):
    if not isinstance(value, str) or any(c.isspace() for c in value):
        return False
    try:
        url = urlsplit(value)
        return url.scheme in ('http', 'https') and bool(url.hostname)
    except ValueError:
        return False


TEXT = rule(lambda v: isinstance(v, str), 'string')
NONEMPTY = rule(lambda v: isinstance(v, str) and bool(v.strip()), 'nonempty string')
UINT = rule(lambda v: type(v) is int and v >= 0, 'nonnegative integer')
POSITIVE = rule(lambda v: type(v) is int and v > 0, 'positive integer')
BOOL = rule(lambda v: type(v) is bool, 'boolean')
SHA = pattern(r'[a-f0-9]{64}')
COMMIT = pattern(r'[a-f0-9]{40}')
VERSION = pattern(r'[0-9]+(?:\.[0-9]+)+[a-zA-Z0-9.+-]*')
UTC = lambda value, path: utc_timestamp(value, path)
URL = rule(http_url, 'HTTP(S) URL')
SNAPSHOT_ID = pattern(r'snapshot-v1-[a-f0-9]{64}')
EXPORT_ID = pattern(r'export-v1-[a-f0-9]{64}')
DATABASE_ID = pattern(r'sqlite-v1-[a-f0-9]{64}')
FILE_FIELDS = {'sha256': SHA, 'bytes': UINT}
FILE = obj(FILE_FIELDS)
SOURCE = obj({
    'url': URL, 'retrieved_at': nullable(rule(timestamp, 'timestamp with seconds and UTC offset')),
    'acquisition': rule(lambda v: v in ('local', 'http'), 'local or http'),
    'etag': nullable(TEXT), 'last_modified': nullable(TEXT),
    'content_length': nullable(rule(lambda v: (type(v) is int and v >= 0)
                                   or (isinstance(v, str) and re.fullmatch(r'[0-9]+', v) is not None),
                                   'nonnegative integer or ASCII decimal string')),
    **FILE_FIELDS, 'compression': rule(lambda v: v in ('gzip', 'none'), 'gzip or none'),
    'decoded': FILE, 'resolved_url': URL,
}, optional=('resolved_url',))
SOURCES = obj({'mpcorb': SOURCE, 'numbered': SOURCE})
SOURCE_HASHES = obj({'mpcorb': SHA, 'numbered': SHA})
COUNTS = obj({key: UINT for key in ('orbital_records', 'master_records', 'known_discovery', 'missing_discovery',
                                   'numbered_orbits', 'unnumbered_orbits', 'unsupported_orbits',
                                   'discovery_records', 'unmatched_discovery_records')})
BASELINE = obj({key: UINT for key in ('orbital_records', 'discovery_records', 'master_records', 'known_discovery')})
EXCLUSION_FIELDS = {'master': obj({'non_elliptic_orbits': UINT}),
                    'discovery': obj({'missing_discovery_date': UINT})}
COMPRESSION = obj({'format': literal('gzip'), 'level': literal(6), 'mtime': literal(0), 'zlib': VERSION})
FILES = obj({'master.jsonl.gz': FILE, 'MPCORB-header.txt': FILE})
SELECTION = obj({'profile': literal('discovery'), 'limit': nullable(POSITIVE),
                 'select': literal('first-known-dates-in-mpcorb-order'), 'sort': literal('disc-ascending-stable')})
SNAPSHOT = obj({
    'snapshot_version': SNAPSHOT_ID,
    'identity': obj({'schema_version': literal(1), 'tool_version': NONEMPTY, 'sources': SOURCE_HASHES}),
    'schema_version': literal(1), 'tool_version': NONEMPTY, 'created_at': UTC,
    'sources': SOURCES, 'counts': COUNTS, 'files': FILES,
    'exclusions': obj(EXCLUSION_FIELDS), 'compression': COMPRESSION,
})
EXPORT_IDENTITY = obj({'snapshot_version': SNAPSHOT_ID, 'tool_version': NONEMPTY,
                       'schema_version': literal(1), 'selection': SELECTION})
EXPORT = obj({
    'data_version': EXPORT_ID, 'identity': EXPORT_IDENTITY, 'snapshot_version': SNAPSHOT_ID,
    'schema_version': literal(1), 'tool_version': NONEMPTY, 'created_at': UTC,
    'selection': SELECTION, 'sources': SOURCES,
    'counts': obj({**{key: UINT for key in ('orbital_records', 'master_records', 'known_discovery', 'missing_discovery',
                                          'numbered_orbits', 'unnumbered_orbits', 'unsupported_orbits',
                                          'discovery_records', 'unmatched_discovery_records')}, 'discovery_export': UINT}),
    'exclusions': obj({**EXCLUSION_FIELDS, 'selection_limit': UINT}),
    'compression': obj({'master': COMPRESSION, 'catalog': COMPRESSION}),
    'artifacts': obj({name: obj({**FILE_FIELDS, 'profile': literal(profile), 'records': UINT}) if profile else FILE
                      for name, profile in [('master.jsonl.gz', 'master'), ('catalog.json', 'discovery'),
                                            ('catalog.json.gz', 'discovery'), ('MPCORB-header.txt', None), ('NOTICE.txt', None)]}),
})
DATABASE = obj({
    'database_version': DATABASE_ID,
    'identity': obj({'database_schema_version': literal(1), 'tool_version': NONEMPTY,
                     'snapshot_version': SNAPSHOT_ID, 'files': FILES, 'notice_sha256': SHA}),
    'database_schema_version': literal(1), 'tool_version': NONEMPTY, 'sqlite_version': VERSION,
    'created_at': UTC, 'snapshot': SNAPSHOT, 'mpcorb_header': TEXT, 'notice': NONEMPTY,
})
RELEASE = obj({
    'release_version': pattern(r'release-v1-[a-f0-9]{64}'), 'release_schema_version': literal(1),
    'identity': obj({'release_schema_version': literal(1), 'dataset_version': pattern(r'data-v1-[a-f0-9]{64}'),
                     'snapshot_version': SNAPSHOT_ID, 'producer': obj({'commit': COMMIT, 'tool_version': NONEMPTY}),
                     'json_schema_version': literal(1), 'database_schema_version': literal(1),
                     'selected_limits': array(POSITIVE), 'notice_sha256': SHA}),
    'dataset_identity': obj({'sources': SOURCE_HASHES}), 'created_at': UTC,
    'runtime': obj({'python': VERSION, 'sqlite': VERSION, 'zlib': VERSION}),
    'sources': SOURCES, 'counts': COUNTS, 'database_version': DATABASE_ID,
    'profiles': mapping(obj({'data_version': EXPORT_ID, 'records': UINT, 'selection': SELECTION})),
    'preparation': obj({'baseline_counts': nullable(BASELINE), 'previous_counts': nullable(COUNTS),
                        'allow_count_decrease': BOOL}),
    'artifacts': mapping(FILE),
})


def validate_contract(kind, value):
    {'snapshot': SNAPSHOT, 'export': EXPORT, 'database': DATABASE, 'release': RELEASE}[kind](value, kind)
    sources = value['snapshot']['sources'] if kind == 'database' else value['sources']
    for name, info in sources.items():
        if info['acquisition'] == 'http' and (info['retrieved_at'] is None or 'resolved_url' not in info):
            raise DataError(f'{kind} HTTP source {name} requires retrieval time and resolved URL')
        if (info['acquisition'] == 'http' and info['content_length'] is not None
                and int(info['content_length']) != info['bytes']):
            raise DataError(f'{kind} HTTP source {name} Content-Length must match raw bytes')
        if info['compression'] == 'none' and info['decoded'] != {key: info[key] for key in ('sha256', 'bytes')}:
            raise DataError(f'{kind} uncompressed source {name} must match decoded metadata')
