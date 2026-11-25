// Isolated headless reader acceptance. Routes serve exact files in-memory;
// no HTTP server, personal browser profile, screenshots or app deployment.
import {chromium,firefox,webkit} from 'playwright';
import fs from 'node:fs/promises';
import path from 'node:path';
import {gunzipSync} from 'node:zlib';
import assert from 'node:assert/strict';
const root=process.cwd(),spec=JSON.parse(await fs.readFile('research/comet-expansion/fixture.json'));
const candidate=new Map();
for(const [name,item] of Object.entries(spec.files))candidate.set(name,gunzipSync(await fs.readFile('research/comet-expansion/payloads/'+item.file)));
const legacy=JSON.parse(await fs.readFile('data/comets/'+spec.legacy.url));
for(const name of [spec.legacy.url,...legacy.objects.flatMap(e=>e.segments.map(s=>s.data.url))])candidate.set(name,await fs.readFile('data/comets/'+name));
const engines={chromium,firefox,webkit};
const selected=process.argv.slice(2).length?process.argv.slice(2):Object.keys(engines);
assert.ok(selected.every(name=>Object.hasOwn(engines,name)));
for(const browserName of selected) {
 const engine=engines[browserName];
 const browser=await engine.launch({headless:true});
 try {
  for(const lane of ['production','candidate']) {
   const context=await browser.newContext(),page=await context.newPage(),errors=[];page.on('pageerror',e=>errors.push(e.message));
   await context.route('**/*',async route=>{
    const url=new URL(route.request().url());
    if(url.origin!=='https://directory.test')return route.abort();
    const name=url.pathname.slice(1);
    if(!name)return route.fulfill({contentType:'text/html',body:'<!doctype html><title>Reader acceptance</title>'});
    if(name.startsWith('consumer/')&&/^[A-Za-z]+\.js$/.test(path.basename(name)))return route.fulfill({contentType:'text/javascript',body:await fs.readFile(path.join(root,name))});
    if(name.startsWith('data/')) {
     const relative=name.slice(5);
     if(relative.includes('..'))return route.abort();
     try {const body=lane==='candidate'?candidate.get(relative):await fs.readFile(path.join(root,'data/comets',relative));
      if(body)return route.fulfill({contentType:'application/json',body});
     }catch{}
    }
    return route.fulfill({status:404,body:''});
   });
   await page.goto('https://directory.test/');
   const result=await page.evaluate(async()=>{
    const {default:Source}=await import('/consumer/CometDirectorySource.js');const s=await Source.openLatest('https://directory.test/data/latest.json');
    let available=0;
    try {for(const e of s.info.objects)if(e.coverage.length){available++;for(const c of e.coverage){const p=await s.positionAt(e.id,c.start);if(!p?.every(Number.isFinite))throw Error('Missing position');} }
     return {pin:s.sourceId,count:s.search('').total,available,defaults:s.info.defaults,discovery:(await s.getDetails('1P')).discovery};
    } finally {s.close();}
   });
   assert.equal(result.pin,lane==='candidate'?spec.candidate.sha256:spec.legacy.sha256);
   assert.equal(result.count,lane==='candidate'?4089:6);assert.equal(result.available,lane==='candidate'?14:6);
   assert.deepEqual(result.defaults,['1P','2P','55P','9P','10P','67P']);assert.deepEqual(result.discovery,legacy.objects[0].discovery);assert.deepEqual(errors,[]);
   console.log(JSON.stringify({browser:browserName,lane,pin:result.pin,objects:result.count,available:result.available}));await context.close();
  }
 }finally{await browser.close();}
}
