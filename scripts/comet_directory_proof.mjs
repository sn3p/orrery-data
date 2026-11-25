// Offline acceptance of complete independent evidence, separate from the small
// reference subset checked by the browser reader on each payload download.
import assert from 'node:assert/strict';
import {owns,directoryPosition} from '../consumer/cometDirectoryContract.js';

export function proofOwner(segments,jd) {
  const matches=segments.filter(s=>owns(s.coverage,jd));
  assert.equal(matches.length,1,`Proof date ${jd} must have exactly one coverage owner (found ${matches.length})`);
  return matches[0];
}

export function verifySegmentProof(segment,descriptor,rows) {
  const label=`Segment ${descriptor.key}`,v=descriptor.validation,g=descriptor.grid;
  assert.ok(Array.isArray(rows),`${label}: missing proof rows`);
  const fractions=v.method==='all-quarter-points-final-serialized'?[.25,.5,.75]:[.5];
  assert.equal(rows.length,v.samples,`${label}: proof sample count`);
  const expected=new Set();
  for(let i=0;i<g.count-1;i++)for(const fraction of fractions)expected.add(g.start+(i+fraction)*g.step);
  assert.equal(expected.size,v.samples,`${label}: validation grid`);
  let maximum=0;
  for(const row of rows) {
    assert.ok(Array.isArray(row)&&row.length===4&&row.every(Number.isFinite),`${label}: invalid proof row`);
    assert.ok(expected.delete(row[0]),`${label}: duplicate or off-grid proof date ${row[0]}`);
    const error=Math.hypot(...directoryPosition(segment.series,row[0]).map((x,i)=>x-row[i+1]));
    maximum=Math.max(maximum,error);
  }
  assert.equal(expected.size,0,`${label}: missing proof dates`);
  assert.ok(maximum<=v.tolerance_au,`${label}: interpolation tolerance`);
  assert.ok(Math.abs(maximum-v.max_error_au)<1e-14,`${label}: recorded maximum differs from independent proof`);
  return rows.length;
}

export function verifyObjectProof(segments,descriptors,proof) {
  const grouped=new Map(descriptors.map(s=>[s.key,[]]));
  if(Array.isArray(proof)) {
    // The pinned first release uses flat rows. Exact endpoint ownership matters.
    for(const row of proof) {
      assert.ok(Array.isArray(row)&&row.length===4&&row.every(Number.isFinite),'Invalid proof row');
      grouped.get(proofOwner(descriptors,row[0]).key).push(row);
    }
  } else {
    // Explicit segment keys can also prove support samples outside owned
    // coverage, including overlapping supports from different source models.
    assert.ok(proof&&typeof proof==='object'&&proof.segments,'Missing segment-scoped proof');
    assert.deepEqual(Object.keys(proof.segments).sort(),[...grouped.keys()].sort(),'Proof segment keys');
    for(const key of grouped.keys())grouped.set(key,proof.segments[key]);
  }
  assert.deepEqual(segments.map(s=>s.key).sort(),[...grouped.keys()].sort(),'Loaded proof segment keys');
  return descriptors.reduce((count,s)=>count+verifySegmentProof(segments.find(x=>x.key===s.key),s,grouped.get(s.key)),0);
}
