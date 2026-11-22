"""Serial, bounded Horizons acquisition; saved responses remain offline-replayable."""

import hashlib
import math
import http.client
from pathlib import Path
import re
import stat
import ssl
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from . import __version__
from .formats import DataError
from .storage import atomic_bytes, atomic_json, loads_json, now, read_json, writer_lock, utc_timestamp
from .paths import validate_writable_path

ENDPOINT = 'https://ssd.jpl.nasa.gov/api/horizons.api'
TARGETS = {'1P': '1P/Halley', '2P': '2P/Encke', '55P': '55P/Tempel-Tuttle'}
START, STEP, COUNT = 2444239.5, 4, 10958
MAX_RESPONSE = 8 * 1024 * 1024
CONTACT = 'https://github.com/sn3p/orrery-data/issues'


def query(object_id, midpoint=False):
    params = dict(format='json', COMMAND=f"'DES={object_id}; CAP;'", EPHEM_TYPE="'VECTORS'",
                  CENTER="'500@10'", START_TIME="'1980-01-03'" if midpoint else "'1980-01-01'",
                  STOP_TIME="'2099-12-30'" if midpoint else "'2100-01-01'", STEP_SIZE="'4 d'",
                  TIME_TYPE="'UT'", OUT_UNITS="'AU-D'", REF_PLANE="'ECLIPTIC'", REF_SYSTEM="'ICRF'",
                  VEC_TABLE="'2'", CSV_FORMAT="'YES'", VEC_CORR="'NONE'", OBJ_DATA="'YES'")
    return ENDPOINT + '?' + urlencode(params)


def parse_response(raw, object_id, midpoint=False, *, targets):
    if len(raw) > MAX_RESPONSE:
        raise DataError('Horizons response exceeds size limit')
    envelope = loads_json(raw)
    if (not isinstance(envelope, dict) or envelope.get('error') or
            envelope.get('signature') not in ({'source': 'NASA/JPL Horizons API', 'version': v} for v in ('1.2', '1.3'))):
        raise DataError('Horizons error or unsupported API signature/version')
    result = envelope.get('result', '')
    if not isinstance(result, str) or result.count('$$SOE') != 1 or result.count('$$EOE') != 1:
        raise DataError('Missing complete Horizons vector table')
    header, body = result.split('$$SOE')
    body, _ = body.split('$$EOE')
    target = re.search(r'^Target body name:\s*(.*?)\s+\{source: (.*?)\}', header, re.M)
    if not target or target[1] != targets[object_id] or not re.fullmatch(r'JPL#[A-Za-z0-9/._-]+', target[2]):
        raise DataError('Horizons returned the wrong target or missing solution')
    required = (r'Center body name:\s*Sun \(10\)', r'Center-site name:\s*BODY CENTER',
                r'Output units\s*:\s*AU-D\s*$', r'Output type\s*:\s*GEOMETRIC cartesian states\s*$',
                r'Output format\s*:\s*2 \(position and velocity\)\s*$',
                r'Reference frame\s*:\s*Ecliptic of J2000.0\s*$', r'JDUT\s*,',
                r'Step-size\s*:\s*5760 minutes\s*$')
    if not all(re.search(pattern, header, re.M) for pattern in required):
        raise DataError('Unexpected Horizons origin/frame/time/units/encoding/grid')
    start = '1980-Jan-03' if midpoint else '1980-Jan-01'
    stop = '2099-Dec-30' if midpoint else '2100-Jan-01'
    for label, date in [('Start time', start), ('Stop  time', stop)]:
        if not re.search(re.escape(label) + r'\s*:\s*A\.D\. ' + date + r' 00:00:00\.0000 UT\s*$', header, re.M):
            raise DataError('Unexpected Horizons requested dates/time scale')
    rows = []
    for line in body.strip().splitlines():
        columns = [v.strip() for v in line.split(',')]
        if len(columns) != 9 or columns[-1] != '':
            raise DataError('Malformed Horizons row')
        row = [float(columns[0]), *map(float, columns[2:8])]
        if not all(math.isfinite(v) for v in row):
            raise DataError('Nonfinite Horizons state')
        if row[0] != START + (2 if midpoint else 0) + len(rows) * STEP:
            raise DataError('Horizons dates are missing, duplicated or out of order')
        rows.append(row)
    if len(rows) != COUNT - int(midpoint):
        raise DataError('Incomplete Horizons coverage')
    # Only operational banners are excluded from scientific identity. Preserve all
    # model, solution, EOP, perturber and query headers, including solution dates.
    scientific = '\n'.join(line.rstrip() for line in header.splitlines()
                           if not line.startswith(('JPL/HORIZONS', 'Ephemeris /', 'Calling TDBMUT')))
    return rows, {'target': target[1], 'solution': target[2], 'header': scientific, 'api_version': envelope['signature']['version']}


def download(url, timeout=90):
    for attempt in range(3):
        try:
            request = Request(url, headers={'User-Agent': f'orrery-data/{__version__} (+{CONTACT})',
                                           'Accept-Encoding': 'identity'})
            with urlopen(request, timeout=timeout) as response:
                if response.status != 200 or response.headers.get('Content-Encoding', 'identity') != 'identity':
                    raise DataError('Unexpected Horizons HTTP response')
                length = response.headers.get('Content-Length')
                if length is not None:
                    if not re.fullmatch(r'[0-9]+', length):
                        raise DataError('Invalid Horizons Content-Length')
                    length = int(length)
                    if length > MAX_RESPONSE:
                        raise DataError('Horizons response exceeds size limit')
                raw = response.read(MAX_RESPONSE + 1)
                if len(raw) > MAX_RESPONSE:
                    raise DataError('Horizons response exceeds size limit')
                if length is not None and len(raw) < length:
                    raise http.client.IncompleteRead(raw, length - len(raw))
                if length is not None and len(raw) > length:
                    raise DataError('Horizons body exceeds declared Content-Length')
                return raw
        except HTTPError as exc:
            code = exc.code
            exc.close()
            if code not in (429, 500, 502, 503, 504) or attempt == 2:
                raise
        except (URLError, TimeoutError, ConnectionError, http.client.IncompleteRead,
                http.client.RemoteDisconnected, ssl.SSLEOFError):
            if attempt == 2:
                raise
        time.sleep(2 ** (attempt + 1))
    raise AssertionError('unreachable')


def acquire(cache, *, refresh=False, offline=False, targets):
    """The cache lock excludes competing access; requests within a call are serial.

    Product refreshes also hold their shared site acquisition lock across caches.
    Explicit refresh bypasses saved bytes.

    Raw records install individually; the accepted public product is only advanced
    after every source and numerical check succeeds. No cache age implies freshness.
    """
    cache = Path(cache)
    validate_writable_path(cache, label='Horizons cache')
    if cache.is_symlink():
        raise DataError('Horizons cache cannot be a symlink')
    if offline and refresh:
        raise DataError('Offline acquisition cannot refresh')
    with writer_lock(cache):
        records = {}
        for object_id in targets:
            for midpoint in (False, True):
                key = object_id + ('-midpoints' if midpoint else '-vectors')
                url = query(object_id, midpoint)
                record_path = cache / (key + '.json')
                if record_path.is_symlink():
                    raise DataError('Horizons cache record cannot be a symlink')
                if record_path.exists() and not refresh:
                    if not stat.S_ISREG(record_path.lstat().st_mode) or record_path.stat().st_size > 4096:
                        raise DataError('Invalid Horizons cache record file')
                    record = read_json(record_path)
                    if not isinstance(record, dict) or set(record) != {'url', 'sha256', 'retrieved_at'}:
                        raise DataError('Invalid Horizons cache record fields')
                    utc_timestamp(record['retrieved_at'], 'Horizons retrieval')
                    if record.get('url') != url or not re.fullmatch(r'[a-f0-9]{64}', record.get('sha256', '')):
                        raise DataError('Invalid Horizons cache record')
                    raw_path = cache / (record['sha256'] + '.raw')
                    if not stat.S_ISREG(raw_path.lstat().st_mode) or raw_path.stat().st_size > MAX_RESPONSE:
                        raise DataError('Invalid Horizons raw cache file')
                    raw = raw_path.read_bytes()
                    if hashlib.sha256(raw).hexdigest() != record['sha256']:
                        raise DataError('Corrupt Horizons cache')
                else:
                    if offline:
                        raise DataError(f'Missing offline Horizons cache: {key}')
                    raw = download(url)
                    parse_response(raw, object_id, midpoint, targets=targets)
                    record = {'url': url, 'sha256': hashlib.sha256(raw).hexdigest(), 'retrieved_at': now()}
                    target = cache / (record['sha256'] + '.raw')
                    if target.exists() or target.is_symlink():
                        if not stat.S_ISREG(target.lstat().st_mode) or target.read_bytes() != raw:
                            raise DataError('Corrupt retained Horizons response')
                    else:
                        atomic_bytes(target, raw)
                    atomic_json(record_path, record)
                rows, provenance = parse_response(raw, object_id, midpoint, targets=targets)
                records[key] = (rows, {**record, **provenance})
        return records
