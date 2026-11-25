// Offline real-file acceptance; never starts a server or falls back to networking.
import fs from 'node:fs/promises';
import path from 'node:path';
import assert from 'node:assert/strict';
import {gunzipSync} from 'node:zlib';
import {fileURLToPath} from 'node:url';
import Source from '../consumer/CometDirectorySource.js';
import {verifyObjectProof} from './comet_directory_proof.mjs';
export async function verify(directory) {
  const root=path.resolve(directory),base='https://candidate.invalid/',original=globalThis.fetch;
  globalThis.fetch=async url=>{
    const u=new URL(url);assert.equal(u.origin,'https://candidate.invalid');
    const name=decodeURIComponent(u.pathname).slice(1),file=path.resolve(root,name);
    assert.ok(file.startsWith(root+path.sep));
    try{return new Response(await fs.readFile(file));}catch{return new Response('',{status:404});}
  };
  let source;
  try {
    source=await Source.openLatest(base+'latest.json');
    const proof=JSON.parse(gunzipSync(await fs.readFile(new URL('../research/comet-expansion/midpoints.json.gz',import.meta.url))));
    let checks=0,available=0;
    for(const e of source.info.objects) {
      if(!e.coverage.length)continue;available++;
      const result=await source.loadWindow(e.id,{start:e.coverage[0].start,end:e.coverage.at(-1).end});
      assert.equal(result.segments.length,e.coverage.length);
      for(const s of result.segments) {
        assert.equal(source.availabilityAt(e.id,s.coverage.start),'available_at_date');
        assert.ok((await source.positionAt(e.id,s.coverage.start)).every(Number.isFinite));
      }
      if(proof[e.id]) {
        const manifest=await source.manifest(e);
        checks+=verifyObjectProof(result.segments,manifest.segments,proof[e.id]);
      }
    }
    assert.deepEqual(source.info.defaults,['1P','2P','55P','9P','10P','67P']);
    if(source.info.comet_contract_version===4){assert.equal(source.info.objects.length,4089);assert.equal(available,14);assert.equal(checks,87656);}
    else {assert.equal(source.info.objects.length,6);assert.equal(available,6);}
    return {pin:source.sourceId,objects:source.info.objects.length,available,independentMidpoints:checks};
  } finally {source?.close();globalThis.fetch=original;}
}
if(process.argv[1]===fileURLToPath(import.meta.url))console.log(JSON.stringify(await verify(process.argv[2])));
