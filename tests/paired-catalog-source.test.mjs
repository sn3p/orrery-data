import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import {execFileSync} from 'node:child_process';
import {openPairedLatest, resolvePairedLatest, validatePairedDescriptor} from '../consumer/PairedCatalogSource.js';

const base='https://sn3p.github.io/orrery-data/';
test('published fixture resolves once, verifies pair, reads rows and rejects corruption', async t => {
  const directory=await fs.mkdtemp(path.join(os.tmpdir(),'paired-reader-'));
  t.after(()=>fs.rm(directory,{recursive:true,force:true}));
  execFileSync('python3',['scripts/update_browser.py','--from-bundle','tests/fixtures/consumer-v1/ties','--output',directory+'/site']);
  const root=directory+'/site', original=globalThis.fetch;
  t.after(()=>{globalThis.fetch=original;});
  let calls=[], corrupt=false;
  globalThis.fetch=async (url, options={})=>{
    const location=String(url);calls.push(location);
    if(options.signal?.aborted) throw new DOMException('Aborted','AbortError');
    assert.ok(location.startsWith(base));
    const bytes=await fs.readFile(path.join(root,location.slice(base.length)));
    return new Response(corrupt && location.includes('/named/mapping-') ? Buffer.from('{}') : bytes);
  };
  const pair=await openPairedLatest(base+'paired/latest.json');
  assert.equal(pair.namedBodies.definitions.bodies.length,13);
  assert.equal(calls.filter(u=>u.endsWith('paired/latest.json')).length,1);
  assert.equal(pair.namedBodies.mapping.asteroid_index_sha256,pair.selection.pin.sha256);
  let count=0;
  for await (const chunk of pair.source.read({start:0,end:pair.source.info.counts.discovery_export})) if(chunk.type==='batch') count+=chunk.records.length;
  assert.equal(count,pair.source.info.counts.discovery_export);
  pair.source.close();
  corrupt=true;
  await assert.rejects(openPairedLatest(base+'paired/latest.json'));
  const controller=new AbortController();controller.abort();
  await assert.rejects(resolvePairedLatest(base+'paired/latest.json',{signal:controller.signal}),{name:'AbortError'});
  const descriptor=JSON.parse(await fs.readFile(root+'/paired/latest.json','utf8'));
  for(const invalid of [{...descriptor,extra:true},{...descriptor,paired_catalogue_version:2},
      {...descriptor,namedBodies:{...descriptor.namedBodies,bytes:131073}},
      {...descriptor,asteroids:{...descriptor.asteroids,url:'http://example.com/index.json'}}]) {
    assert.throws(()=>validatePairedDescriptor(invalid));
  }
  globalThis.fetch=async()=>new Response(' '.repeat(4097));
  await assert.rejects(resolvePairedLatest(base+'paired/latest.json'),/size/);
});

test('paired asteroid references use the shared index size boundary', async () => {
  const {MAX_INDEX_BYTES} = await import('../consumer/contract.js');
  const hash='a'.repeat(64), namedHash='b'.repeat(64);
  const descriptor={paired_catalogue_version:1,
    asteroids:{url:`${base}paired/asteroids/${hash}/index-${hash}.json`,sha256:hash,bytes:MAX_INDEX_BYTES},
    namedBodies:{url:`${base}paired/generations/pair/named/index-${namedHash}.json`,sha256:namedHash,bytes:1}};
  assert.equal(validatePairedDescriptor(descriptor).pin.bytes,MAX_INDEX_BYTES);
  assert.throws(()=>validatePairedDescriptor({...descriptor,asteroids:{...descriptor.asteroids,bytes:MAX_INDEX_BYTES+1}}));
});
