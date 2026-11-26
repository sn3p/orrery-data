"""Compact, producer-owned named-body definitions; independent of discovery dates."""
import math
import re
from .formats import DataError

START, END, EPOCH = 2378496.5, 2488069.5, 2451545.0
NAMES = ('Mercury', 'Venus', 'Earth', 'Mars', 'Jupiter', 'Saturn', 'Uranus', 'Neptune',
         'Ceres', 'Pluto', 'Haumea', 'Makemake', 'Eris')
NUMBERS = (1, 134340, 136108, 136472, 136199)
IDS = tuple('orrery:planet:' + n.lower() for n in NAMES[:8]) + tuple(f'mpc:number:{n}' for n in NUMBERS)
TARGETS = ('199', '299', '399', '4', '5', '6', '7', '8', '1;', '9', '136108;', '136472;', '136199;')
CONVENTIONS = {'origin': 'Sun centre', 'frame': 'Horizons ecliptic J2000 (IAU76 obliquity relative to ICRF)',
               'distance': 'AU', 'angles': 'degrees', 'time': 'TDB Julian days', 'rate_time': 'Julian century (36525 days)',
               'epoch': EPOCH, 'elements': ['a', 'e', 'i', 'ascending_node', 'argument_of_perihelion', 'mean_anomaly']}
EXTRAPOLATION = 'freeze-shape-at-nearest-boundary; continue-mean-anomaly-at-fitted-rate'


def centre_kind(target):
    # Horizons major-body codes 1..9 denote system barycentres; centre codes
    # are three digits and small-body searches carry a semicolon.
    return 'system-barycentre' if target in tuple(str(n) for n in range(1, 10)) else 'body-centre'


def need(ok, label):
    if not ok:
        raise DataError('Invalid named bodies: ' + label)


def keys(value, expected):
    need(isinstance(value, dict) and set(value) == set(expected.split()), expected)


def finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def validate_definitions(value):
    keys(value, 'named_body_version conventions model validation_interval extrapolation evidence_sha256 bodies')
    need(type(value['named_body_version']) is int and value['named_body_version'] == 1, 'version')
    need(value['conventions'] == CONVENTIONS and value['model'] == 'linear-rate-kepler', 'conventions/model')
    need(value['validation_interval'] == [START, END] and value['extrapolation'] == EXTRAPOLATION, 'interval/extrapolation')
    need(isinstance(value['evidence_sha256'], str) and re.fullmatch('[a-f0-9]{64}', value['evidence_sha256']), 'evidence hash')
    need(isinstance(value['bodies'], list) and len(value['bodies']) == 13, 'inventory')
    for i, body in enumerate(value['bodies']):
        keys(body, 'id name classification target centre_kind elements rates sources validation')
        need((body['id'], body['name'], body['target']) == (IDS[i], NAMES[i], TARGETS[i]), 'identity/target')
        need(body['classification'] == ('planet' if i < 8 else 'dwarf-planet'), 'classification')
        need(body['centre_kind'] == centre_kind(TARGETS[i]), 'centre')
        for key in ('elements', 'rates'):
            need(isinstance(body[key], list) and len(body[key]) == 6 and all(finite(v) for v in body[key]), key)
        need(body['rates'][5] > 0, 'forward mean motion')
        for jd in (START, END):
            t = (jd - EPOCH) / 36525
            a, e = (body['elements'][j] + body['rates'][j] * t for j in (0, 1))
            need(a > 0 and 0 <= e < 1, 'elliptic interval')
        keys(body['sources'], 'elements fit validation')
        for source in body['sources'].values():
            keys(source, 'url sha256 retrieved_at target')
            need(isinstance(source['url'], str) and source['url'].startswith('https://ssd.jpl.nasa.gov/api/horizons.api?'), 'source URL')
            need(isinstance(source['sha256'], str) and re.fullmatch('[a-f0-9]{64}', source['sha256']), 'source hash')
            need(isinstance(source['retrieved_at'], str) and isinstance(source['target'], str)
                 and body['name'].lower() in source['target'].lower(), 'source provenance')
        v = body['validation']
        keys(v, 'samples max_position_error_au max_direction_error_degrees meaning')
        need(type(v['samples']) is int and v['samples'] > 0, 'samples')
        need(all(finite(v[k]) and v[k] >= 0 for k in ('max_position_error_au', 'max_direction_error_degrees'))
             and v['meaning'] == 'sampled-residuals-not-guaranteed-bounds', 'accuracy metadata')
    return value


def position(body, jd):
    """XYZ AU and validity status. Outside interval only the shape is frozen."""
    need(finite(jd), 'date')
    bounded = min(END, max(START, jd))
    t = (bounded - EPOCH) / 36525
    a, e, inc, node, arg, mean = [x + rate*t for x, rate in zip(body['elements'], body['rates'])]
    mean += body['rates'][5] * ((jd - bounded) / 36525)
    need(all(math.isfinite(x) for x in (a, e, inc, node, arg, mean)), 'propagation overflow')
    M = math.radians(mean % 360)
    E = M
    for _ in range(32):
        delta = (E - e * math.sin(E) - M) / (1 - e * math.cos(E))
        E -= delta
        if abs(delta) < 1e-13:
            break
    need(abs(E - e*math.sin(E) - M) < 1e-11, 'Kepler convergence')
    x, y = a*(math.cos(E)-e), a*math.sqrt(1-e*e)*math.sin(E)
    w, O, i = map(math.radians, (arg, node, inc))
    cw, sw, cO, sO, ci, si = math.cos(w), math.sin(w), math.cos(O), math.sin(O), math.cos(i), math.sin(i)
    xyz = [(cw*cO-sw*sO*ci)*x+(-sw*cO-cw*sO*ci)*y,
           (cw*sO+sw*cO*ci)*x+(-sw*sO+cw*cO*ci)*y, sw*si*x+cw*si*y]
    return {'position': xyz, 'status': 'validated-interval' if START <= jd <= END else 'approximate-extrapolation'}
