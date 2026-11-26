import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import zlib from 'node:zlib';
import crypto from 'node:crypto';
import {validateNamedDefinitions,validateNamedMapping,namedPosition,loadNamedBodies} from '../consumer/NamedBodySource.js';
const definition=JSON.parse(fs.readFileSync(new URL('../research/named-bodies/definitions.json',import.meta.url)));
const evidence=JSON.parse(zlib.gunzipSync(fs.readFileSync(new URL('../research/named-bodies/evidence.json.gz',import.meta.url))));
const data=()=>structuredClone(definition);
test('all thirteen real reference grids match documented residuals',()=>{
 const d=validateNamedDefinitions(data());
 d.bodies.forEach((b,i)=>{
  let max=0;
  for(const [jd,...xyz] of evidence[i].validation){const p=namedPosition(b,jd);assert.equal(p.status,'validated-interval');max=Math.max(max,Math.hypot(...p.position.map((x,k)=>x-xyz[k])));}
  assert(Math.abs(max-b.validation.max_position_error_au)<1e-9,b.name);
  for(const jd of [-1930999.5,5373484.5]){assert.equal(namedPosition(b,jd).status,'approximate-extrapolation');assert.notDeepEqual(namedPosition(b,jd).position,namedPosition(b,jd+20).position);}
 });
});
const source=()=>({sourceId:'a'.repeat(64),info:{catalog_id:'export-v1-'+ 'b'.repeat(64),snapshot_version:'snapshot-v1-'+ 'c'.repeat(64),counts:{discovery_export:10}},lifetime:new AbortController(),closed:false});
function mapping(s){return {named_body_mapping_version:1,catalog_id:s.info.catalog_id,asteroid_index_sha256:s.sourceId,snapshot_version:s.info.snapshot_version,records:10,members:definition.bodies.slice(8).map((b,i)=>({id:b.id,status:'present',ordinal:i,discovery_jd:2400000,reason:null}))};}
test('strict identity, stale generation and absence semantics',()=>{
 const s=source();assert(Object.isFrozen(validateNamedMapping(mapping(s),s)));
 for(const change of [v=>v.asteroid_index_sha256='d'.repeat(64),v=>v.members[0].ordinal=10,v=>v.members[1].ordinal=0,v=>v.members[0].status='unknown']){const v=mapping(s);change(v);assert.throws(()=>validateNamedMapping(v,s));}
 const absent=mapping(s);absent.members[0]={...absent.members[0],status:'absent-from-export',ordinal:null,discovery_jd:null,reason:'not-in-master'};validateNamedMapping(absent,s);
 const badAbsence=structuredClone(absent);badAbsence.members[0].reason='selection-limit';assert.throws(()=>validateNamedMapping(badAbsence,s));
 for(const change of [d=>d.bodies.pop(),d=>d.bodies[2].centre_kind='system-barycentre',d=>d.bodies[0].rates[1]=Infinity]){const d=data();change(d);assert.throws(()=>validateNamedDefinitions(d));}
});
test('pinned generation reads atomically and fails corruption, cancellation and closed source',async()=>{
 const s=source(),files=new Map();
 function add(v,prefix){const raw=Buffer.from(JSON.stringify(v)),sha256=crypto.createHash('sha256').update(raw).digest('hex'),url=prefix+sha256+'.json';files.set('/'+url,raw);return {url,sha256,bytes:raw.length};}
 const d=add(data(),'definitions-'),m=add(mapping(s),'mapping-'),ref=add({named_body_product_version:1,definitions:d,mapping:m},'index-');
 const original=globalThis.fetch;
 try{
  globalThis.fetch=async url=>new Response(files.get(new URL(url).pathname));
  const result=await loadNamedBodies(s,ref,'https://example.invalid/'+ref.url);assert.equal(result.definitions.bodies.length,13);assert(Object.isFrozen(result));
  files.set('/'+m.url,Buffer.from('bad'));await assert.rejects(loadNamedBodies(s,ref,'https://example.invalid/'+ref.url));assert.equal(result.mapping.members.length,5);
  const c=new AbortController();c.abort();await assert.rejects(loadNamedBodies(s,ref,'https://example.invalid/'+ref.url,{signal:c.signal}),{name:'AbortError'});
  s.closed=true;await assert.rejects(loadNamedBodies(s,ref,'https://example.invalid/'+ref.url));
 }finally{globalThis.fetch=original;}
});
test('an in-flight obsolete generation cannot replace the accepted result',async()=>{
 const s=source(),files=new Map();
 const add=(value,prefix)=>{const raw=Buffer.from(JSON.stringify(value)),sha256=crypto.createHash('sha256').update(raw).digest('hex'),url=prefix+sha256+'.json';files.set('/'+url,raw);return {url,sha256,bytes:raw.length};};
 const d=add(data(),'definitions-'),m=add(mapping(s),'mapping-'),ref=add({named_body_product_version:1,definitions:d,mapping:m},'index-');
 const original=globalThis.fetch;
 try{
  globalThis.fetch=async url=>new Response(files.get(new URL(url).pathname));
  const accepted=await loadNamedBodies(s,ref,'https://example.invalid/'+ref.url);
  globalThis.fetch=async url=>{const raw=files.get(new URL(url).pathname);if(new URL(url).pathname==='/'+m.url)s.lifetime.abort();return new Response(raw);};
  await assert.rejects(loadNamedBodies(s,ref,'https://example.invalid/'+ref.url),{name:'AbortError'});
  assert.equal(accepted.mapping.members.length,5);
 }finally{globalThis.fetch=original;}
});
test('reference location is bound before fetch, with canonical relative resolution',async()=>{
 const s=source(),original=globalThis.fetch;let calls=0;
 globalThis.fetch=async()=>{calls++;throw new Error('reached fetch');};
 const ref={url:'index.json',sha256:'a'.repeat(64),bytes:2};
 try{
  for(const url of ['https://wrong.invalid/other.json','https://wrong.invalid/index.json?other=1','http://remote.invalid/index.json']){
   await assert.rejects(loadNamedBodies(s,ref,url));assert.equal(calls,0);
  }
  await assert.rejects(loadNamedBodies(s,{...ref,url:'https://trusted.invalid/index.json'},'https://wrong.invalid/index.json'));assert.equal(calls,0);
  for(const [location,url] of [['index.json','https://valid.invalid/path/index.json'],['https://VALID.invalid:443/a/../index.json','https://valid.invalid/index.json']]){
   await assert.rejects(loadNamedBodies(s,{...ref,url:location},url),/reached fetch/);
  }
  assert.equal(calls,2);
 }finally{globalThis.fetch=original;}
});
