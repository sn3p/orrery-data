import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import {createHash} from 'node:crypto';
import Source,{T,END} from '../consumer/CometHistorySource.js';
import {CONVENTIONS} from '../consumer/cometSourceCore.js';
const base='https://example.test/sub/comets/';
const template=JSON.parse(await fs.readFile('tests/fixtures/comet-history-index.json'));
const ref=(body,prefix)=>{const sha256=createHash('sha256').update(body).digest('hex');return {url:`${prefix}${sha256}.json`,sha256,bytes:body.length};};
function fixture(mutate=()=>{}) {
  const index=structuredClone(template), files=new Map();
  for(const o of index.objects) {
    for(const s of o.segments) {
      const count=(s.sample_coverage[1]-s.sample_coverage[0])/4+1;
      const value={comet_contract_version:s.payload_contract_version,id:o.id,conventions:CONVENTIONS,
        series:{start:s.sample_coverage[0],step:4,states:Array.from({length:count},()=>[0,0,0,0,0,0])}};
      if(s.key==='historical')value.segment='historical';
      for(const c of s.key==='historical'?s.validation.checks:[s.validation]) {
        c.max_error_au=0; c.references=c.references.map(r=>[r[0],0,0,0]);
      }
      mutate('payload',value,o,s);
      const body=Buffer.from(JSON.stringify(value));s.data=ref(body,'objects/');files.set(s.data.url,body);
    }
    if(o.transition)for(const c of o.transition.comparisons){c.position_difference_au=0;c.velocity_difference_au_per_day=0;}
  }
  mutate('index',index);
  const body=Buffer.from(JSON.stringify(index)), pin=ref(body,'index-');files.set(pin.url,body);
  files.set('latest.json',JSON.stringify({comet_contract_version:3,index:pin}));
  return {files,index};
}
function transport(t,f,gate) {
  const calls=[];
  t.mock.method(globalThis,'fetch',async(url,options)=>{
    const name=new URL(url).href.slice(base.length);calls.push(name);
    if(gate)await gate(name,options);
    return f.files.has(name)?new Response(f.files.get(name)):new Response('',{status:404});
  });return calls;
}
const open=()=>Source.openLatest(base+'latest.json');
test('metadata is eager, files lazy, modern owns exact transition and final date',async t=>{
  const f=fixture(),calls=transport(t,f),s=await open();assert.equal(calls.length,2);
  assert.equal(s.info.objects.length,6);assert.equal(s.info.objects[4].discovery.events[0].date.day,null);
  for(const id of ['1P','55P']) {
    assert.equal((await s.load(id,{jd:T-1e-6})).segment,'historical');
    assert.equal((await s.load(id,{jd:T})).comet_contract_version,2);
    assert.deepEqual(await s.positionAt(id,T+1),[0,0,0]);
    assert.deepEqual(await s.positionAt(id,END),[0,0,0]);
    assert.equal(await s.positionAt(id,END+1e-6),null);
    const start=s.object(id).segments[0].coverage.start;
    assert.deepEqual(await s.positionAt(id,start),[0,0,0]);
    assert.equal(await s.positionAt(id,start-1e-6),null);
    const strips=await s.pathSegments(id,T-2,T+2);
    assert.deepEqual(strips.map(p=>p.key),['historical','modern']);
    assert.ok(strips[0].points.at(-1)[0]<T);assert.equal(strips[1].points[0][0],T);
    assert.deepEqual((await s.pathSegments(id,T,T+1)).map(p=>p.key),['modern']);
  }
  assert.equal(await s.positionAt('2P',T-1),null);
  await assert.rejects(s.load('1P'),/date/);
  s.close();await assert.rejects(s.positionAt('1P',T),{name:'AbortError'});
});
test('malformed rehashed metadata, coverage and provenance fail before object reads',async t=>{
  const mutations=[
    i=>i.objects[0].discovery.events[0].people='bad',
    i=>i.objects[0].discovery.events[0].date.day=1,
    i=>i.objects[0].discovery.catalogue_provenance.retrieved_at='unknown',
    i=>i.objects[0].discovery.extra=true,
    i=>i.objects[0].discovery.event_inventory_complete=true,
    i=>i.objects[2].segments[0].coverage.end_inclusive=true,
    i=>i.objects[2].segments[0].sample_coverage[1]=T,
    i=>i.objects[0].segments[0].provenance.vectors.solution='JPL#76',
    i=>i.objects[0].segments[0].provenance.vectors.header+='changed model',
    i=>i.objects[0].segments[0].provenance.vectors.api_version='99',
    i=>i.objects[0].segments[0].data.bytes=2*1024*1024+1,
    i=>i.objects[0].transition.owner='historical',
    i=>i.objects[0].segments.push(i.objects[0].segments[0]),
  ];
  for(const mutation of mutations){const f=fixture((kind,v)=>{if(kind==='index')mutation(v);});const calls=transport(t,f);await assert.rejects(open());assert.equal(calls.length,2);}
});
test('actual seam velocity fails even if position checks and declared limits pass, in either load order',async t=>{
  for(const first of ['historical','modern']) {
    const f=fixture((kind,v,o,s)=>{if(kind==='payload'&&o.id==='1P'&&s.key==='historical')v.series.states.at(-1)[3]=.00005;});
    const calls=transport(t,f);const source=await open();await assert.rejects(source.loadSegment('1P',first),/transition evidence/);
    const second=first==='historical'?'modern':'historical';
    await assert.rejects(source.loadSegment('1P',second),/transition evidence/);
    const before=calls.length;
    await assert.rejects(source.pathSegments('1P',T-1,T+1),/transition evidence/);
    assert.equal(calls.length,before+2); // inconsistent pair was evicted, neither side silently reused
    source.close();
  }
});
test('two transfers maximum, subscriber cancellation, queued abort and retry',async t=>{
  const f=fixture(),gates=[];
  transport(t,f,async(name,options)=>{if(name.startsWith('objects/'))await new Promise(resolve=>gates.push({resolve,signal:options.signal}));});
  const s=await open(),abort=new AbortController();
  const a=s.loadSegment('10P','modern',{signal:abort.signal});const rejected=assert.rejects(a,{name:'AbortError'});
  const b=s.loadSegment('10P','modern'),c=s.loadSegment('2P','modern');
  const queued=new AbortController();const q=s.loadSegment('9P','modern',{signal:queued.signal});const qRejected=assert.rejects(q,{name:'AbortError'});
  await new Promise(resolve=>setImmediate(resolve));assert.equal(gates.length,2);
  abort.abort();queued.abort();await Promise.all([rejected,qRejected]);assert.equal(gates[0].signal.aborted,false);
  gates[0].resolve();gates[1].resolve();await Promise.all([b,c]);
  const retry=s.loadSegment('9P','modern');await new Promise(resolve=>setImmediate(resolve));assert.equal(gates.length,3);gates[2].resolve();await retry;
  s.close();
});
test('corruption/missing pin assets reject and retry uses same pin; close rejects pending work',async t=>{
  const f=fixture(),calls=transport(t,f),s=await open(),pin=s.sourceId;
  const name=f.index.objects[0].segments[0].data.url,body=f.files.get(name);
  f.files.delete(name);await assert.rejects(s.loadSegment('1P','historical'));
  f.files.set(name,'{}');await assert.rejects(s.loadSegment('1P','historical'));
  f.files.set(name,body);await s.loadSegment('1P','historical');assert.equal(s.sourceId,pin);
  assert.equal(calls.filter(n=>n===name).length,3);
  let release;transport(t,f,async n=>{if(n.startsWith('objects/'))await new Promise(r=>{release=r;});});
  const pending=s.loadSegment('2P','modern');const rejected=assert.rejects(pending,{name:'AbortError'});
  await new Promise(resolve=>setImmediate(resolve));s.close();await rejected;release();
});
const real=process.env.COMET_HISTORY_CANDIDATE;
test('real serialized candidate: all segment references, seams, support-only boundaries and modern bytes',{skip:!real},async t=>{
  const files=new Map();const latest=JSON.parse(await fs.readFile(`${real}/latest.json`));
  const index=JSON.parse(await fs.readFile(`${real}/${latest.index.url}`));
  for(const name of ['latest.json',latest.index.url,...index.objects.flatMap(o=>o.segments.map(s=>s.data.url))])files.set(name,await fs.readFile(`${real}/${name}`));
  transport(t,{files});const s=await open();
  for(const o of index.objects) {
    for(const segment of o.segments)await s.loadSegment(o.id,segment.key);
    const modern=o.segments.at(-1);assert.deepEqual(files.get(modern.data.url),await fs.readFile(`data/comets/${modern.data.url}`));
    if(o.transition){assert.equal((await s.load(o.id,{jd:T-1e-6})).segment,'historical');assert.equal((await s.load(o.id,{jd:T})).comet_contract_version,2);await s.pathSegments(o.id,T-2,T+2);}
  }
  s.close();
});

test('review: historical-only and retry reads cannot bypass transition validation',async t=>{
  const f=fixture((kind,v,o,s)=>{if(kind==='payload'&&o.id==='1P'&&s.key==='historical')v.series.states.at(-1)[3]=.00005;});
  transport(t,f);const s=await open();
  await assert.rejects(s.positionAt('1P',T-2),/transition evidence/);
  await assert.rejects(s.loadSegment('1P','historical'),/transition evidence/);
  await assert.rejects(s.positionAt('1P',T-2),/transition evidence/);
  s.close();
});
test('review: path boundary emits T only from modern data',async t=>{
  const f=fixture();transport(t,f);const s=await open();
  const strips=await s.pathSegments('55P',T-2,T+2);
  assert.ok(strips[0].points.every(p=>p[0]<T));
  assert.equal(strips[0].points.length,2);
  assert.ok(T-strips[0].points.at(-1)[0]<1e-9);
  assert.ok(strips[0].points.at(-1)[0]>strips[0].points[0][0]);
  assert.deepEqual(strips.flatMap(strip=>strip.points.filter(p=>p[0]===T).map(()=>strip.key)),['modern']);
  s.close();
});
test('review: failed path cancels its sibling subscriber',async t=>{
  const f=fixture(), historical=f.index.objects[0].segments[0].data.url;
  f.files.set(historical,'{}');let signal,release;
  transport(t,f,async(name,options)=>{
    if(name.startsWith('objects/')&&name!==historical) {
      signal=options.signal;await new Promise(r=>{release=r;});
    }
  });const s=await open();
  try { await assert.rejects(s.pathSegments('1P',T-2,T+2));assert.equal(signal.aborted,true); }
  finally {release?.();s.close();}
});

test('complete fixed inventory stays cached without eviction or extra downloads',async t=>{
  const f=fixture(),calls=transport(t,f),s=await open();
  for(const o of f.index.objects)for(const seg of o.segments)await s.loadSegment(o.id,seg.key);
  assert.equal(calls.length,10);
  for(const o of f.index.objects)for(const seg of o.segments)await s.loadSegment(o.id,seg.key);
  assert.equal(calls.length,10);s.close();
});
test('concurrent pair callers both reject corruption and release their subscribers',async t=>{
  const f=fixture(), old=f.index.objects[0].segments[0].data.url;
  f.files.set(old,'{}');let release, signal;
  transport(t,f,async(n,options)=>{if(n.startsWith('objects/')&&n!==old){signal=options.signal;await new Promise(r=>{release=r;});}});
  const s=await open();
  // Both are public pair subscribers. Failure cancels path-owned subscribers,
  // and the other read independently rejects its own invalid pair.
  const path=assert.rejects(s.pathSegments('1P',T-2,T+2));
  const other=assert.rejects(s.loadSegment('1P','modern'));
  await Promise.all([path,other]);assert.equal(signal.aborted,true);release();s.close();
});
