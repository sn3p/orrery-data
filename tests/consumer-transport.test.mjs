import test from 'node:test';
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { aborted, fetchLatest, fetchVerified } from '../consumer/transport.js';
import { fetchVerified as legacyFetchVerified } from '../consumer/CatalogSource.js';
import { requireValue, keys, parseJSON, verifyBytes } from '../consumer/contract.js';

const url = 'https://example.test/data.json';
const body = new TextEncoder().encode('{}');
const ref = { url: 'data.json', bytes: body.length, sha256: createHash('sha256').update(body).digest('hex') };

for (const product of ['catalogue', 'comet']) {
  const title = product[0].toUpperCase() + product.slice(1);
  test(`${product} validation, HTTP, checksum and cancellation errors retain product context`, async t => {
    assert.throws(() => requireValue(false, 'states', product), { message: `Invalid ${product} states.` });
    assert.throws(() => keys({}, ['id'], [], product), { message: `Invalid ${product} fields.` });
    assert.throws(() => parseJSON('{"id":1,"id":2}', product), { message: `Invalid ${product} duplicate JSON key.` });
    await assert.rejects(verifyBytes(body, { ...ref, sha256: '0'.repeat(64) }, product),
      { message: `Invalid ${product} checksum for data.json.` });
    assert.equal(aborted(product).message, `${title} read cancelled.`);
    t.mock.method(globalThis, 'fetch', async () => new Response(null, { status: 404 }));
    await assert.rejects(fetchLatest(url, undefined, product), { message: `${title} discovery failed (404).` });
    await assert.rejects(fetchVerified(ref, url, undefined, product), { message: `${title} request failed (404): data.json` });
    const controller = new AbortController(); controller.abort();
    await assert.rejects(fetchVerified(ref, url, controller.signal, product),
      { name: 'AbortError', message: `${title} read cancelled.` });
  });

  test(`${product} rejects null bodies with contextual descriptor and payload errors`, async t => {
    for (const status of [200, 204]) {
      t.mock.method(globalThis, 'fetch', async () => new Response(null, { status }));
      await assert.rejects(fetchLatest(url, undefined, product),
        { name: 'Error', message: `Invalid ${product} latest descriptor size (missing response body).` });
      await assert.rejects(fetchVerified(ref, url, undefined, product),
        { name: 'Error', message: `Invalid ${product} byte length for data.json (missing response body).` });
    }
  });

  test(`${product} enforces decoded size bounds and releases body readers`, async t => {
    let response;
    t.mock.method(globalThis, 'fetch', async () => response);
    response = new Response(' '.repeat(4097));
    await assert.rejects(fetchLatest(url, undefined, product), { message: `Invalid ${product} latest descriptor size.` });
    assert.equal(response.body.locked, false);
    for (const text of ['{', '{} ']) {
      response = new Response(text);
      await assert.rejects(fetchVerified(ref, url, undefined, product), { message: `Invalid ${product} byte length for data.json.` });
      assert.equal(response.body.locked, false);
    }
    response = new Response(body);
    assert.deepEqual(await fetchVerified(ref, url, undefined, product), body);
    assert.equal(response.body.locked, false);
  });
}

test('cancellation during a body read releases resources with product context', async t => {
  const controller = new AbortController();
  let cancelled = 0, released = 0;
  t.mock.method(globalThis, 'fetch', async () => ({ ok: true, body: { getReader: () => ({
    read: async () => { controller.abort(); return { done: false, value: body }; },
    cancel: async () => { cancelled++; }, releaseLock: () => { released++; },
  }) } }));
  await assert.rejects(fetchVerified(ref, url, controller.signal, 'comet'),
    { name: 'AbortError', message: 'Comet read cancelled.' });
  assert.equal(cancelled, 1); assert.equal(released, 1);
});

test('legacy catalogue helper keeps its three-argument API and catalogue behavior', async t => {
  assert.equal(legacyFetchVerified.length, 3);
  t.mock.method(globalThis, 'fetch', async () => new Response(body));
  assert.deepEqual(await legacyFetchVerified(ref, url), body);
  t.mock.method(globalThis, 'fetch', async () => new Response(null, { status: 404 }));
  // The optional shared-transport context is not part of the legacy API.
  await assert.rejects(legacyFetchVerified(ref, url, undefined, 'comet'),
    { message: 'Catalogue request failed (404): data.json' });
});


test('cancellation during reader cleanup skips checksum work', async t => {
  const controller = new AbortController();
  let reads = 0, released = 0, digests = 0;
  t.mock.method(crypto.subtle, 'digest', async () => { digests++; return new ArrayBuffer(32); });
  t.mock.method(globalThis, 'fetch', async () => ({ ok: true, body: { getReader: () => ({
    read: async () => reads++ ? { done: true } : { done: false, value: body },
    cancel: async () => controller.abort(), releaseLock: () => { released++; },
  }) } }));
  await assert.rejects(fetchVerified(ref, url, controller.signal, 'comet'),
    { name: 'AbortError', message: 'Comet read cancelled.' });
  assert.equal(digests, 0); assert.equal(released, 1);
});

test('shared URL policy permits HTTPS and canonical loopback HTTP only',async()=>{
 const {validatedURL}=await import('../consumer/transport.js');
 for(const url of ['https://example.org/a','http://localhost/a','http://127.1/a','http://[::1]/a'])assert.equal(typeof validatedURL(url).href,'string');
 for(const url of ['http://example.org/a','https://user:secret@example.org/a','file:///a','http://[::2]/a'])assert.throws(()=>validatedURL(url));
});
