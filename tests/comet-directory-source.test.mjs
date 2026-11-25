import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import {gunzipSync} from 'node:zlib';
import {createHash} from 'node:crypto';
import Source from '../consumer/CometDirectorySource.js';
import {CONVENTIONS} from '../consumer/cometSourceCore.js';
import {verify} from '../scripts/verify_comet_directory.mjs';
const base='https://example.test/comets/';
const spec=JSON.parse(await fs.readFile('research/comet-expansion/fixture.json'));
const template=new Map();
for(const [name,item] of Object.entries(spec.files))template.set(name,gunzipSync(await fs.readFile('research/comet-expansion/payloads/'+item.file)));
const legacy=JSON.parse(await fs.readFile('data/comets/'+spec.legacy.url));
template.set(spec.legacy.url,await fs.readFile('data/comets/'+spec.legacy.url));
for(const e of legacy.objects)for(const s of e.segments)template.set(s.data.url,await fs.readFile('data/comets/'+s.data.url));
const encode=v=>Buffer.from(JSON.stringify(v));
function put(files,v,prefix){const b=encode(v),sha256=createHash('sha256').update(b).digest('hex');const ref={url:prefix+sha256+'.json',sha256,bytes:b.length};files.set(ref.url,b);return ref;}
function fixture(edit=()=>{}){
 const files=new Map(template),index=JSON.parse(files.get(spec.candidate.url));edit(index,files);
 files.set('latest.json',encode({comet_contract_version:4,index:put(files,index,'index-')}));return {files,index};
}
function transport(t,f,gate){const calls=[];t.mock.method(globalThis,'fetch',async(url,options)=>{const name=new URL(url).href.slice(base.length);calls.push(name);if(gate)await gate(name,options);return new Response(f.files.get(name)??'',{status:f.files.has(name)?200:404});});return calls;}
const open=()=>Source.openLatest(base+'latest.json');
test('existing production reader acceptance uses real unchanged files',async()=>{const v=await verify('data/comets');assert.equal(v.available,6);});
test('exact expanded fixture preserves legacy bytes, defaults, IDs and all measured midpoint errors',async t=>{
 const f={files:new Map(template)},calls=transport(t,f),s=await open();t.after(()=>s.close());assert.equal(s.sourceId,spec.candidate.sha256);
 assert.equal(calls.length,3);assert.equal(s.search('',{limit:30}).total,4089);assert.deepEqual(s.info.defaults,legacy.objects.map(e=>e.id));
 for(const e of legacy.objects){assert.equal(s.getObject(e.id).id,e.id);assert.deepEqual((await s.getDetails(e.id)).discovery,e.discovery);for(const seg of e.segments){assert.deepEqual(await s.positionAt(e.id,seg.coverage.start),(await s.legacy.positionAt(e.id,seg.coverage.start)));}}
 const proof=JSON.parse(gunzipSync(await fs.readFile('research/comet-expansion/midpoints.json.gz')));
 const {interpolateComet}=await import('../consumer/cometSourceCore.js');let count=0;
 for(const [id,rows] of Object.entries(proof)) {const e=s.getObject(id),result=await s.loadWindow(id,{start:e.coverage[0].start,end:e.coverage.at(-1).end});let max=0;for(const r of rows){max=Math.max(max,Math.hypot(...interpolateComet(result.segments[0].series,r[0]).map((x,i)=>x-r[i+1])));count++;}assert.ok(max<=.000052);assert.ok(Math.abs(max-(await s.manifest(e)).segments[0].validation.max_error_au)<1e-14);}
 assert.equal(count,87656);assert.equal(s.info.objects.filter(e=>e.coverage.length).length,14);
});
function shortFixture(edit=()=>{}) {return fixture((index,files)=>{
 const e=index.objects.find(e=>e.designation==='1995 O1');e.aliases.push('a shared alias');e.assessment={state:'available',reason:null,scope:[],evidence:null};
 const other=index.objects.find(e=>e.designation==='73P');other.aliases.push('a shared alias');other.parent_id=e.id;
 const segments=[{key:'early',start:2450000,step:1,count:3,inclusive:false},{key:'dense',start:2450002,step:.25,count:5,inclusive:true},{key:'after-gap',start:2450010,step:1,count:3,inclusive:true}].map(x=>{
   const coverage={start:x.start,end:x.start+x.step*(x.count-1),end_inclusive:x.inclusive};
   const series={start:x.start,step:x.step,states:Array.from({length:x.count},(_,i)=>[i*x.step+x.start-2450000,0,0,1,0,0])};
   const data=put(files,{comet_contract_version:4,id:e.id,conventions:CONVENTIONS,series},'objects/');
   const p={url:'https://ssd.jpl.nasa.gov/api/horizons.api?fixture',sha256:'a'.repeat(64),target:e.name,solution:'fixture',header:'synthetic fixture, not a scientific trajectory',retrieved_at:'2026-11-25T00:00:00Z',api_version:'1.3'};
   return {key:x.key,join:x.key==='dense'?'continuous':null,coverage,grid:{start:x.start,step:x.step,count:x.count},data,provenance:{vectors:p,checks:[{...p}]},validation:{method:'all-midpoints-final-serialized',samples:x.count-1,tolerance_au:.000052,max_error_au:0,references:[[x.start+x.step/2,x.start+x.step/2-2450000,0,0]]}};
 });
 e.coverage=segments.map(s=>s.coverage);const manifest={comet_contract_version:4,id:e.id,segments};edit(index,e,manifest,files);e.trajectory={kind:'manifest',manifest:put(files,manifest,'manifests/')};
 });}
test('short coverage, dense blocks, gaps, aliases, fragments and separate status axes',async t=>{
 const f=shortFixture(),calls=transport(t,f),s=await open();t.after(()=>s.close());const e=s.getObject('1995 O1');
 assert.throws(()=>s.getObject('a shared alias'),/ambiguous/);assert.equal(s.search('a shared alias').total,2);
 assert.equal(s.search('1995O1').items[0].id,e.id);assert.equal(s.getObject('73P').parent_id,e.id);
 const cursor=s.search('',{limit:3}).nextCursor;assert.equal(s.search('',{cursor,limit:3}).items.length,3);assert.throws(()=>s.search('different',{cursor}),/cursor/);
 assert.equal(s.availabilityAt(e.id,2450004),'outside_coverage');assert.equal(await s.positionAt(e.id,2450004),null);assert.equal(calls.length,3);
 assert.deepEqual(await s.positionAt(e.id,2450002),[2,0,0]);assert.equal(calls.filter(x=>x.startsWith('objects/')).length,2);
 const strips=await s.pathSegments(e.id,2450000,2450012);assert.equal(strips.length,3);assert.ok(strips[0].points.at(-1)[0]<2450002);assert.equal(strips[1].points[0][0],2450002);assert.equal(strips[2].points[0][0],2450010);
 const unknown=s.info.objects.find(e=>e.assessment.state==='not_yet_assessed');assert.equal(s.availabilityAt(unknown.id,2450000),'not_yet_assessed');
 const pending=s.getObject('1996 B2');assert.equal(s.availabilityAt(pending.id,2450000),'under_assessment');
 s.close();assert.throws(()=>s.search(''),{name:'AbortError'});await assert.rejects(s.positionAt(e.id,2450000),{name:'AbortError'});
});
test('unsupported requires a scoped assessment and never follows from a load error',async t=>{
 const f=fixture(index=>{const e=index.objects.find(e=>e.designation==='3D');e.assessment={state:'unsupported',reason:'no-source-solution',evidence:{assessed_at:'2026-11-25',policy:'fixture-only',source:'fixture',sha256:'a'.repeat(64)},scope:[{start:2450000,end:2450002,end_inclusive:true}]};});
 transport(t,f);const s=await open();t.after(()=>s.close());assert.equal(s.availabilityAt('3D',2450001),'unsupported');assert.equal(s.availabilityAt('3D',2500000),'not_yet_assessed');
 const e=s.getObject('12P'),manifest=JSON.parse(f.files.get(e.trajectory.manifest.url)),name=manifest.segments[0].data.url,bytes=f.files.get(name);f.files.delete(name);
 await assert.rejects(s.positionAt('12P',2450000),/404/);assert.equal(s.availabilityAt('12P',2450000),'available_at_date');f.files.set(name,bytes);assert.ok(await s.positionAt('12P',2450000));
});
test('rehashed contract violations fail closed',async t=>{
 for(const mutate of [
   i=>i.defaults.reverse(),i=>i.objects[0].coverage[0].start++,i=>i.objects[0].id=i.objects[1].id,
   i=>i.objects.find(e=>!e.coverage.length).assessment.state='available',
   i=>i.objects.find(e=>!e.coverage.length).assessment={state:'unsupported',reason:'no test',scope:[],evidence:null},
 ]){const f=fixture(mutate);transport(t,f);await assert.rejects(open());}
 for(const mutate of [
   (i,e,m)=>m.segments[0].provenance.checks[0].header+='different dynamics',
   (i,e,m)=>m.segments[0].provenance.vectors.target='wrong comet',
   (i,e,m)=>m.segments[0].grid.step=0,
   (i,e,m)=>m.segments[0].coverage.end_inclusive=true,
   (i,e,m)=>m.segments[0].validation.max_error_au=1,
 ]){const f=shortFixture(mutate);transport(t,f);let s;try{s=await open();await assert.rejects(s.positionAt('1995 O1',2450001));}catch(error){assert.match(error.message,/Invalid/);}finally{s?.close();}}
});
test('shared cancellation, failed-read retry, close and bounded cache',async t=>{
 const f=shortFixture();let release,entered;const ready=new Promise(r=>entered=r);const hold=new Promise(r=>release=r);
 const calls=transport(t,f,async name=>{if(name.startsWith('objects/')){entered();await hold;}});const s=await open();t.after(()=>s.close());
 const a=new AbortController(),b=new AbortController();const p=s.positionAt('1995 O1',2450001,{signal:a.signal}),q=s.positionAt('1995 O1',2450001,{signal:b.signal});await ready;a.abort();await assert.rejects(p,{name:'AbortError'});release();assert.deepEqual(await q,[1,0,0]);assert.equal(calls.filter(n=>n.startsWith('objects/')).length,2);
 s.cacheBudget=1;await assert.rejects(s.loadWindow('1995 O1',{start:2450000,end:2450012}),/budget/);
 s.close();await assert.rejects(s.positionAt('1995 O1',2450001),{name:'AbortError'});
});
test('continuous joins authenticate actual endpoint position and velocity; explicit separate strips may jump',async t=>{
 for(const component of [0,3]) {
  const f=shortFixture((i,e,m,files)=>{const s=m.segments[1];const payload=JSON.parse(files.get(s.data.url));payload.series.states[0][component]+=1;s.data=put(files,payload,'objects/');
   // Preserve an independently valid reference away from the changed interval.
   s.validation.references=[[2450002.875,2.875,0,0]];
  });transport(t,f);const source=await open();await assert.rejects(source.positionAt('1995 O1',2450000),/join evidence/);source.close();
 }
 const f=shortFixture((i,e,m)=>{m.segments[1].join='separate';});transport(t,f);const s=await open();assert.equal((await s.pathSegments('1995 O1',2450000,2450003)).length,2);s.close();
});
test('transient failure cannot become unsupported, stale generation cursors and corrupt payloads reject',async t=>{
 const f=fixture(i=>{const e=i.objects.find(e=>!e.coverage.length);e.assessment={state:'unsupported',reason:'temporary-timeout',scope:[{start:1,end:2,end_inclusive:true}],evidence:{assessed_at:'2026-11-25',policy:'x',source:'x',sha256:'a'.repeat(64)}};});transport(t,f);await assert.rejects(open(),/scoped rejection/);
 const good=shortFixture();transport(t,good);const s=await open();const cursor=s.search('',{limit:1}).nextCursor;assert.throws(()=>s.search('',{cursor:{...cursor,pin:'0'.repeat(64)}}),/cursor/);
 const e=s.getObject('1995 O1'),m=JSON.parse(good.files.get(e.trajectory.manifest.url));const name=m.segments[0].data.url;const bytes=good.files.get(name);good.files.set(name,Buffer.alloc(bytes.length,32));await assert.rejects(s.positionAt(e.id,2450000),/checksum/);good.files.set(name,bytes);assert.deepEqual(await s.positionAt(e.id,2450000),[0,0,0]);s.close();
});
test('decimal sampling steps preserve inclusive endpoints without extrapolation',async t=>{
 const f=shortFixture((index,e,m,files)=>{
   const s=m.segments[0];s.grid={start:2450000,step:.1,count:3};s.coverage={start:2450000,end:2450000.2,end_inclusive:true};s.join=null;
   const series={start:2450000,step:.1,states:[[0,0,0,1,0,0],[.1,0,0,1,0,0],[.2,0,0,1,0,0]]};
   s.data=put(files,{comet_contract_version:4,id:e.id,conventions:CONVENTIONS,series},'objects/');s.validation.references=[[2450000.05,.05,0,0]];
   m.segments=[s];e.coverage=[s.coverage];
 });transport(t,f);const s=await open();assert.deepEqual(await s.positionAt('1995 O1',2450000.2),[.2,0,0]);assert.equal(await s.positionAt('1995 O1',2450000.2000001),null);
 assert.deepEqual((await s.pathSegments('1995 O1',2450000,2450000.2))[0].points.at(-1),[2450000.2,.2,0,0]);s.close();
});
test('cache eviction releases block descriptors as well as payloads',async t=>{
 const f=shortFixture();transport(t,f);const s=await open();await s.positionAt('1995 O1',2450001);assert.ok(s.entries.some(e=>e.kind==='block'));
 s.cacheBudget=1;s.trim();assert.equal(s.cache.size,0);assert.equal(s.entries.filter(e=>e.kind==='block').length,0);
 s.cacheBudget=16*1024*1024;assert.deepEqual(await s.positionAt('1995 O1',2450001),[1,0,0]);s.close();
});
