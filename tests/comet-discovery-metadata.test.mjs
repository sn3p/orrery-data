import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import crypto from 'node:crypto';
import {validateDiscoveryMetadata,loadDiscoveryMetadata,catalogueDate} from '../consumer/cometDiscoveryMetadata.js';
import Directory from '../consumer/CometDirectorySource.js';
const read=p=>JSON.parse(fs.readFileSync(p));
const root='data/comets/',descriptor=read(root+'latest.json'),directory=read(root+descriptor.index.url),legacy=read(root+directory.legacy.url);
const metadata=read('research/comet-discovery/metadata.json');
const validate=v=>validateDiscoveryMetadata(v,directory,legacy,descriptor.index.sha256);
test('whole catalogue metadata preserves originals and trajectory inventory',()=>{
  const before=JSON.stringify(directory),v=validate(structuredClone(metadata));
  assert.equal(v.objects.length,4089);assert.equal(JSON.stringify(directory),before);
  for(const old of legacy.objects)assert.deepEqual(v.objects.find(x=>x.id===old.id).discovery,old.discovery);
  assert.ok(Object.isFrozen(v.objects));
});
test('reject corrupt pins, sources, dates, identity and legacy provenance',()=>{
  for(const mutate of [v=>v.directory_sha256='0'.repeat(64),v=>v.objects.pop(),v=>v.objects[0].discovery.events[0].date.year=0,
    v=>v.sources.halley.url='https://wrong.example',v=>v.objects.find(x=>x.id==='c1000068').discovery.events[0].date.day=12,
    v=>v.objects.find(x=>x.id==='c1000068').discovery.events[0].sources=['missing'],v=>v.objects.find(x=>x.id==='c1000068').discovery.events[0].kind='perihelion']) {
    const v=structuredClone(metadata);mutate(v);assert.throws(()=>validate(v));
  }
});
test('unknown and approximate event dates survive without a fabricated instant',()=>{
  const v=structuredClone(metadata),e=v.objects.find(x=>x.id==='c1000068').discovery.events[0];
  e.date=null;e.certainty='uncertain';assert.equal(validate(v).objects.find(x=>x.id==='c1000068').discovery.events[0].date,null);
  const a=structuredClone(metadata);a.objects.find(x=>x.id==='c1000068').discovery.events[0].certainty='approximate';assert.doesNotThrow(()=>validate(a));
});
test('verified loader binds sidecar to current source and respects cancellation',async t=>{
  const old=globalThis.fetch;t.after(()=>globalThis.fetch=old);
  const raw=fs.readFileSync('research/comet-discovery/metadata.json');
  const ref={sha256:crypto.createHash('sha256').update(raw).digest('hex'),bytes:raw.length};
  globalThis.fetch=async()=>new Response(raw);
  const source=new Directory('https://example.test/index.json',descriptor.index.sha256,directory,{info:legacy,close(){}});
  const before=source.availabilityAt('c1003667',2451545);
  const result=await loadDiscoveryMetadata(source,ref,'https://example.test/metadata.json');
  assert.equal(result.objects.length,4089);assert.equal(source.availabilityAt('c1003667',2451545),before);
  source.close();await assert.rejects(loadDiscoveryMetadata(source,ref,'https://example.test/metadata.json'));
});

test('pending metadata download aborts on source close or caller cancellation',async t=>{
  const old=globalThis.fetch;t.after(()=>globalThis.fetch=old);
  for(const closeSource of [true,false]) {
    let observed;
    globalThis.fetch=async(url,{signal})=>{
      observed=signal;
      return new Promise((resolve,reject)=>signal.addEventListener('abort',()=>reject(new DOMException('Aborted','AbortError')),{once:true}));
    };
    const source=new Directory('https://example.test/index.json',descriptor.index.sha256,directory,{info:legacy,close(){}});
    const controller=new AbortController(),ref={sha256:'a'.repeat(64),bytes:20};
    const pending=loadDiscoveryMetadata(source,ref,'https://example.test/metadata.json',{signal:controller.signal});
    if(closeSource)source.close();else controller.abort();
    await assert.rejects(pending);assert.equal(observed.aborted,true);
    source.close();observed=null;
    await assert.rejects(loadDiscoveryMetadata(source,ref,'https://example.test/metadata.json'));
    assert.equal(observed,null);
  }
});

test('Python/JS date projections share calendar-neutral expectations',()=>{
  for(const {raw,expected} of read('tests/comet-discovery-dates.json'))assert.deepEqual(catalogueDate(raw),expected);
});
test('calendar rules distinguish Gregorian from unspecified historical dates',()=>{
  for(const [calendar,year,day,valid] of [['historical-unspecified',1700,29,true],['gregorian',1700,29,false],['historical-unspecified',1701,29,false],['historical-unspecified',1700,30,false]]) {
    const v=structuredClone(metadata),e=v.objects.find(x=>x.id==='c1000068').discovery.events[0];
    Object.assign(e,{certainty:'documented',date:{year,month:2,day,precision:'day',calendar,year_numbering:'astronomical'}});
    if(valid)assert.doesNotThrow(()=>validate(v));else assert.throws(()=>validate(v));
  }
});
test('reject mismatched catalogue date and impossible source check dates',()=>{
  for(const mutate of [v=>v.objects.find(x=>x.id==='c1000068').discovery.catalogue_date.year=1800,
    v=>v.objects.find(x=>x.id==='c1000068').discovery.catalogue_date=null,
    ...['2026-13-45','2026-02-30','1900-02-29'].map(day=>v=>v.sources['pons-brooks-history'].checked_on=day)]) {
    const v=structuredClone(metadata);mutate(v);assert.throws(()=>validate(v));
  }
});
test('loader errors identify metadata and the requested URL',async t=>{
  const old=globalThis.fetch;t.after(()=>globalThis.fetch=old);
  const source=new Directory('https://example.test/index.json',descriptor.index.sha256,directory,{info:legacy,close(){}});
  t.after(()=>source.close());
  const url='https://example.test/metadata.json',ref={sha256:'a'.repeat(64),bytes:2};
  for(const response of [()=>new Response('',{status:404}),()=>new Response('x'),()=>new Response('{}')]) {
    globalThis.fetch=async()=>response();
    await assert.rejects(loadDiscoveryMetadata(source,ref,url),e=>e.message.includes(url)&&!e.message.includes('undefined')&&/comet discovery metadata/i.test(e.message));
  }
  const controller=new AbortController();controller.abort();
  globalThis.fetch=async()=>assert.fail('pre-cancelled request fetched');
  await assert.rejects(loadDiscoveryMetadata(source,ref,url,{signal:controller.signal}),e=>e.name==='AbortError'&&e.message==='Comet discovery metadata read cancelled.');
});
