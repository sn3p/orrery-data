"""Strict offline validation of the three named-body Horizons query grids."""
import json
import math
import re
from urllib.parse import parse_qs, urlparse
from .named_bodies import TARGETS, NAMES, START, END, EPOCH, need


def parse_source(raw, url, index, kind):
    params=dict(format='json', COMMAND=repr(TARGETS[index]), EPHEM_TYPE=repr('ELEMENTS' if kind=='elements' else 'VECTORS'),
                CENTER="'500@10'",OUT_UNITS="'AU-D'",REF_PLANE="'ECLIPTIC'",REF_SYSTEM="'ICRF'",CSV_FORMAT="'YES'",TIME_TYPE="'TDB'",OBJ_DATA="'YES'")
    if kind=='elements':params['TLIST']="'2451545.0'"
    else:params.update(START_TIME="'1800-01-09'" if kind=='validation' else "'1800-01-01'",STOP_TIME="'2100-01-01'",
                       STEP_SIZE="'17 d'" if kind=='validation' else "'1000'",VEC_TABLE="'2'",VEC_CORR="'NONE'")
    parsed=urlparse(url)
    need(kind in ('elements','fit','validation') and parsed.scheme=='https' and parsed.netloc=='ssd.jpl.nasa.gov'
         and parsed.path=='/api/horizons.api' and not parsed.fragment
         and parse_qs(parsed.query)=={k:[v] for k,v in params.items()},'Horizons query contract')
    envelope=json.loads(raw)
    need(not envelope.get('error') and envelope.get('signature') in ({'source':'NASA/JPL Horizons API','version':v} for v in ('1.2','1.3')),'Horizons envelope')
    result=envelope.get('result','')
    need(isinstance(result,str) and result.count('$$SOE')==result.count('$$EOE')==1,'Horizons table')
    header,table=result.split('$$SOE');table=table.split('$$EOE')[0]
    target=re.search(r'^Target body name:\s*(.*?)\s+\{source: ([A-Za-z0-9#./_-]+)\}',header,re.M)
    name=NAMES[index];target_id=TARGETS[index]
    expected=(rf'{target_id[:-1]} {name} \([^\n]+\)' if target_id.endswith(';') else
              rf'{name}'+(' Barycenter' if index in (3,4,5,6,7,9) else '')+rf' \({target_id}\)')
    need(target is not None and re.fullmatch(expected,target[1]),'Horizons target/centre kind')
    patterns=[r'Center body name:\s*Sun \(10\)',r'Center-site name:\s*BODY CENTER',
              r'Reference frame\s*:\s*Ecliptic of J2000.0\s*$',r'\bJDTDB,',
              r'Output units\s*:\s*AU-D'+(r', deg, Julian Day Number \(Tp\)' if kind=='elements' else '')+r'\s*$',
              r'Output type\s*:\s*GEOMETRIC '+('osculating elements' if kind=='elements' else 'cartesian states')+r'\s*$',
              r'Output format\s*:\s*'+('10' if kind=='elements' else r'2 \(position and velocity\)')+r'\s*$']
    need(all(re.search(p,header,re.M) for p in patterns),'Horizons units/frame/time/type')
    rows=[]
    for line in table.strip().splitlines():
        columns=[v.strip() for v in line.split(',')]
        need(len(columns)==(15 if kind=='elements' else 9) and columns[-1]=='','Horizons columns')
        row=[float(v) for j,v in enumerate(columns[:-1]) if j!=1]
        need(all(math.isfinite(x) for x in row),'Horizons finite rows');rows.append(row)
    count=1 if kind=='elements' else 6446 if kind=='validation' else 1001
    first=EPOCH if kind=='elements' else 2378504.5 if kind=='validation' else START
    step=0 if kind=='elements' else 17 if kind=='validation' else (END-START)/1000
    need(len(rows)==count and all(abs(r[0]-(first+i*step))<1e-8 for i,r in enumerate(rows)),'Horizons date grid')
    return rows,target[0]
