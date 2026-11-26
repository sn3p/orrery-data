// Exercise the real hash-verifying reader against local immutable files, no HTTP server.
import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import {loadNamedBodies} from '../consumer/NamedBodySource.js';
import CatalogSource from '../consumer/CatalogSource.js';
const [candidate,browser]=process.argv.slice(2);
const latest=JSON.parse(fs.readFileSync(path.join(browser,'latest.json')));
const info=JSON.parse(fs.readFileSync(path.join(browser,latest.index.url)));
const raw=fs.readFileSync(path.join(candidate,'index.json'));
const ref={url:'index.json',bytes:raw.length,sha256:crypto.createHash('sha256').update(raw).digest('hex')};
globalThis.fetch=async url=>{const u=new URL(url);return new Response(fs.readFileSync(path.join(u.hostname==='asteroids.invalid'?browser:candidate,u.pathname.slice(1))));};
const source=await CatalogSource.open({...latest.index,url:'https://asteroids.invalid/'+latest.index.url});
const result=await loadNamedBodies(source,ref,'https://candidate.invalid/index.json');
console.log(JSON.stringify({bodies:result.definitions.bodies.length,mapping:result.mapping}));
source.close();
