#!/usr/bin/env python3
"""Build reviewable definitions/evidence from retained Horizons research responses.
No source acquisition and no publication. Output is research/named-bodies.
"""
import argparse
import gzip
import hashlib
import json
import math
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from orrery_data.named_bodies import (NAMES, IDS, TARGETS, START, END, CONVENTIONS,
                                      EXTRAPOLATION, need, position, centre_kind, validate_definitions)
from orrery_data.storage import json_bytes
from orrery_data.named_body_sources import parse_source


def build(cache, output):
    fits = json.loads((cache/'linear-rate-report.json').read_bytes())
    dense = json.loads((cache/'dense-report.json').read_bytes())
    evidence, bodies = [], []
    for i, (fit, measured) in enumerate(zip(fits, dense)):
        need(fit['name'] == measured['name'] == NAMES[i], 'evidence order')
        sources, tables = {}, {}
        for key, suffix in [('elements', 'elements'), ('fit', 'vectors'), ('validation', 'dense')]:
            stem = fit['name'].lower()+'-'+suffix
            raw = (cache/(stem+'.json')).read_bytes()
            source = json.loads((cache/(stem+'.source.json')).read_bytes())
            need(hashlib.sha256(raw).hexdigest() == source['sha256'], 'raw source hash')
            tables[key], target = parse_source(raw, source['url'], i, key)
            source['target'] = target
            sources[key] = source
        need(len(tables['elements']) == 1 and len(tables['fit']) == 1001 and len(tables['validation']) == 6446, 'source grid')
        need(tables['fit'][0][0] == START and tables['fit'][-1][0] == END, 'endpoints')
        training = {r[0] for r in tables['fit'][1::2]}
        refs = [r[:4] for r in tables['fit'][::2]] + [r[:4] for r in tables['validation'] if r[0] not in training]
        body = dict(id=IDS[i], name=NAMES[i], classification='planet' if i < 8 else 'dwarf-planet', target=TARGETS[i],
                    centre_kind=centre_kind(TARGETS[i]),
                    elements=fit['parameters'][:6], rates=fit['parameters'][6:], sources=sources)
        errors, angles = [], []
        for row in refs:
            xyz = position(body, row[0])['position']; actual = row[1:4]
            errors.append(math.dist(xyz, actual))
            cos = sum(a*b for a,b in zip(xyz,actual))/math.sqrt(sum(a*a for a in xyz)*sum(a*a for a in actual))
            angles.append(math.degrees(math.acos(min(1,max(-1,cos)))))
        body['validation'] = dict(samples=len(refs), max_position_error_au=max(errors), max_direction_error_degrees=max(angles),
                                  meaning='sampled-residuals-not-guaranteed-bounds')
        bodies.append(body)
        evidence.append(dict(id=IDS[i], sources=sources, elements=tables['elements'][0], fit=tables['fit'], validation=refs))
    need(len(bodies) == 13, 'all thirteen')
    raw = gzip.compress(json_bytes(evidence), mtime=0)
    definitions = dict(named_body_version=1, conventions=CONVENTIONS, model='linear-rate-kepler',
                       validation_interval=[START, END], extrapolation=EXTRAPOLATION,
                       evidence_sha256=hashlib.sha256(raw).hexdigest(), bodies=bodies)
    validate_definitions(definitions)
    output.mkdir(parents=True, exist_ok=True)
    (output/'evidence.json.gz').write_bytes(raw)
    (output/'definitions.json').write_bytes(json_bytes(definitions))

if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cache', type=Path, required=True)
    parser.add_argument('--output', type=Path, default=Path('research/named-bodies'))
    args=parser.parse_args();build(args.cache,args.output)
