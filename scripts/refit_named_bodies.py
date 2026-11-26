#!/usr/bin/env python3
"""Reproduce the compact fit from committed source evidence (NumPy/SciPy required).
Outputs numerical agreement; does not modify accepted definitions or publish data.
"""
import gzip
import json
from pathlib import Path
import sys
import numpy as np
from scipy.optimize import least_squares
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from orrery_data.named_bodies import validate_definitions
from orrery_data.storage import read_json, verify_file
ROOT=Path(__file__).resolve().parents[1]/'research/named-bodies'


def positions(p, jd):
    t=(np.asarray(jd)-2451545.0)/36525
    a,e,i,O,w,M=np.asarray(p[:6])[:,None]+np.asarray(p[6:])[:,None]*t
    i,O,w,M=np.radians([i,O,w,M]);M=(M+np.pi)%(2*np.pi)-np.pi;E=M.copy()
    for _ in range(20):E-=(E-e*np.sin(E)-M)/(1-e*np.cos(E))
    x=a*(np.cos(E)-e);y=a*np.sqrt(1-e*e)*np.sin(E)
    cw,sw,cO,sO,ci,si=np.cos(w),np.sin(w),np.cos(O),np.sin(O),np.cos(i),np.sin(i)
    return np.array([(cw*cO-sw*sO*ci)*x+(-sw*cO-cw*sO*ci)*y,(cw*sO+sw*cO*ci)*x+(-sw*sO+cw*cO*ci)*y,sw*si*x+cw*si*y]).T


def main():
    d=validate_definitions(read_json(ROOT/'definitions.json'))
    path=ROOT/'evidence.json.gz';verify_file(path,{'sha256':d['evidence_sha256'],'bytes':path.stat().st_size})
    evidence=json.loads(gzip.decompress(path.read_bytes()))
    for body,source in zip(d['bodies'],evidence):
        e=source['elements'];rows=np.array(source['fit'])[1::2]
        seed=np.array([e[10],e[1],e[3],e[4],e[5],e[8],0,0,0,0,0,e[7]*36525])
        fit=least_squares(lambda p:(positions(p,rows[:,0])-rows[:,1:4]).ravel(),seed,x_scale='jac',max_nfev=2000,ftol=1e-11,xtol=1e-11,gtol=1e-11)
        difference=float(np.max(np.abs(fit.x-np.array(body['elements']+body['rates']))))
        print(json.dumps({'id':body['id'],'converged':bool(fit.success),'max_coefficient_difference':difference}),flush=True)
        if not fit.success or difference>1e-7:raise ValueError('Fit differs; review numerical environment/input before accepting')
if __name__=='__main__':main()
