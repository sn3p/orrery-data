import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import crypto from 'node:crypto';
import {validateDiscoveryMetadata,loadDiscoveryMetadata} from '../consumer/cometDiscoveryMetadata.js';
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
