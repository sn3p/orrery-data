import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import CometSource, { interpolateComet, validateComet } from '../consumer/CometSource.js';

const root = path.resolve('data/comets');
const base = 'https://example.test/sub/comets/';
async function fixture(t, fault) {
  const calls = [];
  t.mock.method(globalThis, 'fetch', async (url, options) => {
    const relative = new URL(url).href.slice(base.length); calls.push(relative);
    if (options?.signal?.aborted) throw new DOMException('cancelled', 'AbortError');
    if (fault) { const result = await fault(relative, options); if (result) return result; }
    try { return new Response(await fs.readFile(path.join(root, relative))); }
    catch { return new Response('', { status: 404 }); }
  });
  return calls;
}

test('pinned subpath catalogue fetches only selected objects and matches numerical references', async t => {
  const calls = await fixture(t);
  const source = await CometSource.openLatest(base + 'latest.json');
  assert.equal(calls.length, 2);
  for (const entry of source.info.objects) {
    const value = await source.load(entry.id);
    for (const row of entry.validation.references) assert.ok(
      Math.hypot(...interpolateComet(value.series, row[0]).map((v, k) => v - row[k + 1])) <= 0.000052);
    assert.deepEqual(interpolateComet(value.series, entry.coverage[0]), value.series.states[0].slice(0, 3));
    assert.deepEqual(interpolateComet(value.series, entry.coverage[1]), value.series.states.at(-1).slice(0, 3));
    assert.throws(() => interpolateComet(value.series, entry.coverage[1] + 1));
    assert.equal(await source.load(entry.id), value);
  }
  assert.equal(calls.length, 5);
  assert.ok(Object.isFrozen(source.info));
  source.close(); await assert.rejects(source.load('1P'), { name: 'AbortError' });
});

test('corrupt and missing objects fail; retry retains the original pin', async t => {
  let bad = true;
  await fixture(t, relative => bad && relative.startsWith('objects/') ? new Response('{}') : null);
  const source = await CometSource.openLatest(base + 'latest.json');
  const pin = source.sourceId;
  await assert.rejects(source.load('2P'));
  bad = false;
  assert.equal((await source.load('2P')).id, '2P');
  assert.equal(source.sourceId, pin);
  await assert.rejects(source.load('unknown'));
});

test('cancellation and source close reject delayed responses before caching', async t => {
  let release;
  const calls = await fixture(t, async relative => {
    if (relative.startsWith('objects/')) await new Promise(resolve => { release = resolve; });
  });
  const source = await CometSource.openLatest(base + 'latest.json');
  const controller = new AbortController();
  const pending = source.load('1P', { signal: controller.signal });
  controller.abort(); release();
  await assert.rejects(pending, { name: 'AbortError' });
  const pending2 = source.load('1P'); source.close(); release();
  await assert.rejects(pending2, { name: 'AbortError' });
  await assert.rejects(source.load('1P'), { name: 'AbortError' });
  assert.equal(calls.filter(v => v.startsWith('objects/')).length, 2);
});

test('untrusted URL and oversized or unsupported descriptor fail before object reads', async t => {
  let body = 'x'.repeat(4097), calls = 0;
  t.mock.method(globalThis, 'fetch', async () => { calls++; return new Response(body); });
  await assert.rejects(CometSource.openLatest('http://example.test/latest.json')); assert.equal(calls, 0);
  await assert.rejects(CometSource.openLatest(base + 'latest.json'));
  body = JSON.stringify({ comet_contract_version: 2, index: {} });
  await assert.rejects(CometSource.openLatest(base + 'latest.json'));
  assert.equal(calls, 2);
});

test('rehashed malformed provenance is rejected at the index boundary', async t => {
  const { createHash } = await import('node:crypto');
  const original = JSON.parse(await fs.readFile(path.join(root, 'latest.json')));
  const originalIndex = JSON.parse(await fs.readFile(path.join(root, original.index.url)));
  for (const [key, bad] of [['api_version', '9'], ['retrieved_at', 'yesterday'],
    ['retrieved_at', '2026-02-30T00:00:00Z'], ['url', 'https://wrong.example/']]) {
    const index = structuredClone(originalIndex);
    index.objects[0].provenance.vectors[key] = bad;
    const body = Buffer.from(JSON.stringify(index));
    const sha256 = createHash('sha256').update(body).digest('hex');
    const latest = { comet_contract_version: 1, index: { url: `index-${sha256}.json`, sha256, bytes: body.length } };
    t.mock.method(globalThis, 'fetch', async url => new Response(new URL(url).pathname.endsWith('latest.json')
      ? JSON.stringify(latest) : body));
    await assert.rejects(CometSource.openLatest(base + 'latest.json'), /provenance/);
  }
});

test('concurrent readers share object verification and one cancellation preserves other readers', async t => {
  let release, transportSignal;
  const calls = await fixture(t, async (relative, options) => {
    if (relative.startsWith('objects/')) {
      transportSignal = options.signal;
      await new Promise(resolve => { release = resolve; });
    }
  });
  const source = await CometSource.openLatest(base + 'latest.json');
  const controller = new AbortController();
  const cancelled = source.load('1P', { signal: controller.signal });
  const first = source.load('1P'), second = source.load('1P');
  const rejection = assert.rejects(cancelled, { name: 'AbortError' });
  controller.abort();
  await rejection;
  assert.equal(transportSignal.aborted, false);
  assert.equal(calls.filter(v => v.startsWith('objects/')).length, 1);
  release();
  const [a, b] = await Promise.all([first, second]);
  assert.equal(a, b);
  assert.equal(await source.load('1P'), a);
  assert.equal(calls.filter(v => v.startsWith('objects/')).length, 1);
  source.close();
});

test('all cancelled readers abort shared work and permit retry before stale work settles', async t => {
  const gates = [];
  const calls = await fixture(t, async (relative, options) => {
    if (relative.startsWith('objects/')) await new Promise(resolve => gates.push({ release: resolve, signal: options.signal }));
  });
  const source = await CometSource.openLatest(base + 'latest.json');
  const a = new AbortController(), b = new AbortController();
  const first = source.load('2P', { signal: a.signal }), second = source.load('2P', { signal: b.signal });
  const rejected = [assert.rejects(first, { name: 'AbortError' }), assert.rejects(second, { name: 'AbortError' })];
  a.abort(); assert.equal(gates[0].signal.aborted, false);
  b.abort(); assert.equal(gates[0].signal.aborted, true);
  await Promise.all(rejected);
  const retry = source.load('2P');
  assert.equal(gates.length, 2);
  gates[0].release(); gates[1].release();
  assert.equal((await retry).id, '2P');
  assert.equal(calls.filter(v => v.startsWith('objects/')).length, 2);
  source.close();
});

test('shared failures evict the request and source close cancels every reader', async t => {
  let corrupt = true, release, transportSignal;
  const calls = await fixture(t, async (relative, options) => {
    if (relative.startsWith('objects/')) {
      transportSignal = options.signal;
      await new Promise(resolve => { release = resolve; });
      if (corrupt) return new Response('{}');
    }
  });
  const source = await CometSource.openLatest(base + 'latest.json');
  const failures = [assert.rejects(source.load('55P')), assert.rejects(source.load('55P'))];
  release(); await Promise.all(failures);
  assert.equal(calls.filter(v => v.startsWith('objects/')).length, 1);
  corrupt = false;
  const closed = [assert.rejects(source.load('55P'), { name: 'AbortError' }),
    assert.rejects(source.load('55P'), { name: 'AbortError' })];
  source.close(); assert.equal(transportSignal.aborted, true);
  await Promise.all(closed);
  release();
  await assert.rejects(source.load('55P'), { name: 'AbortError' });
  assert.equal(calls.filter(v => v.startsWith('objects/')).length, 2);
});

test('a pre-aborted reader never starts or joins object work', async t => {
  const calls = await fixture(t);
  const source = await CometSource.openLatest(base + 'latest.json');
  const controller = new AbortController(); controller.abort();
  await assert.rejects(source.load('1P', { signal: controller.signal }), { name: 'AbortError' });
  assert.equal(calls.length, 2);
  source.close();
});

test('shared discovery transport preserves asteroid fixtures and rejects bounded descriptor errors', async t => {
  const { default: CatalogSource } = await import('../consumer/CatalogSource.js');
  const directory = path.resolve('tests/fixtures/browser-v1/ties');
  const requests = [];
  let fault;
  t.mock.method(globalThis, 'fetch', async (url, options) => {
    requests.push({ url: String(url), options });
    options.signal?.throwIfAborted();
    const name = new URL(url).pathname.replace('/data/', '');
    return new Response(name === 'latest.json' && fault ? fault : await fs.readFile(path.join(directory, name)));
  });
  const source = await CatalogSource.openLatest('https://example.test/data/latest.json');
  const rows = [];
  for await (const event of source.read({ start: 0, end: source.info.counts.discovery_export })) {
    if (event.type === 'batch') rows.push(...event.records);
  }
  const expected = JSON.parse(await fs.readFile('tests/fixtures/consumer-v1/ties/full/catalog.json'));
  assert.deepEqual(rows, expected);
  assert.equal(requests[0].options.cache, 'no-cache');
  assert.equal(requests[0].options.redirect, 'error');
  source.close();
  fault = ' '.repeat(4097);
  await assert.rejects(CatalogSource.openLatest('https://example.test/data/latest.json'), /latest descriptor size/);
  fault = '{"browser_contract_version":1,"browser_contract_version":1,"index":{}}';
  await assert.rejects(CatalogSource.openLatest('https://example.test/data/latest.json'), /duplicate JSON key/);
  const controller = new AbortController(); controller.abort();
  const count = requests.length;
  await assert.rejects(CatalogSource.openLatest('https://example.test/data/latest.json', { signal: controller.signal }),
    { name: 'AbortError' });
  assert.equal(requests.length, count);
});

test('synchronous fetch interception can cancel before subscriber registration', async t => {
  const controller = new AbortController();
  let transportSignal, intercept = true;
  const calls = await fixture(t, (relative, options) => {
    if (intercept && relative.startsWith('objects/')) {
      transportSignal = options.signal;
      controller.abort();
    }
  });
  const source = await CometSource.openLatest(base + 'latest.json');
  await assert.rejects(source.load('1P', { signal: controller.signal }), { name: 'AbortError' });
  assert.equal(transportSignal.aborted, true);
  intercept = false;
  assert.equal((await source.load('1P')).id, '1P');
  assert.equal(calls.filter(v => v.startsWith('objects/')).length, 2);
  source.close();
});

function cancelAtVerifiedContinuation(t, controller) {
  const digest = crypto.subtle.digest.bind(crypto.subtle);
  const decode = TextDecoder.prototype.decode;
  let decodedAfterAbort = 0;
  t.mock.method(crypto.subtle, 'digest', async (...args) => {
    const result = await digest(...args);
    // Schedule after verifyBytes and fetchVerified resume, before their caller
    // resumes. This exercises the cancellation gap between promise continuations.
    queueMicrotask(() => queueMicrotask(() => queueMicrotask(() => controller.abort())));
    return result;
  });
  t.mock.method(TextDecoder.prototype, 'decode', function (...args) {
    if (controller.signal.aborted) decodedAfterAbort++;
    return decode.apply(this, args);
  });
  return () => decodedAfterAbort;
}

test('last cancellation between verified bytes and object continuation skips decoding', async t => {
  await fixture(t);
  const source = await CometSource.openLatest(base + 'latest.json');
  const controller = new AbortController();
  const decodedAfterAbort = cancelAtVerifiedContinuation(t, controller);
  await assert.rejects(source.load('1P', { signal: controller.signal }), { name: 'AbortError' });
  // Subscriber rejection is immediate; let any stale shared continuation finish.
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(controller.signal.aborted, true);
  assert.equal(decodedAfterAbort(), 0);
  source.close();
});

test('cancellation between verified bytes and index continuation skips decoding', async t => {
  await fixture(t);
  const controller = new AbortController();
  const decodedAfterAbort = cancelAtVerifiedContinuation(t, controller);
  await assert.rejects(CometSource.openLatest(base + 'latest.json', { signal: controller.signal }), { name: 'AbortError' });
  assert.equal(controller.signal.aborted, true);
  assert.equal(decodedAfterAbort(), 0);
});

test('comet adapter errors keep comet context at discovery, selection and load boundaries', async t => {
  let failDescriptor = true, failObjects = false;
  await fixture(t, relative => (failDescriptor && relative === 'latest.json')
    || (failObjects && relative.startsWith('objects/')) ? new Response(null, { status: 404 }) : null);
  await assert.rejects(CometSource.openLatest(base + 'latest.json'), { message: 'Comet discovery failed (404).' });
  failDescriptor = false;
  const source = await CometSource.openLatest(base + 'latest.json');
  await assert.rejects(source.load('unknown'), { message: 'Invalid comet selection.' });
  failObjects = true;
  await assert.rejects(source.load('1P'), /^Error: Comet request failed \(404\): objects\//);
  source.close();
  await assert.rejects(source.load('1P'), { name: 'AbortError', message: 'Comet read cancelled.' });
});


test('v1 object validation rejects catalogue-only identities', async () => {
  const directory = path.resolve('data/comets-v2');
  const latest = JSON.parse(await fs.readFile(path.join(directory, 'latest.json')));
  const index = JSON.parse(await fs.readFile(path.join(directory, latest.index.url)));
  for (const entry of index.objects) {
    const value = JSON.parse(await fs.readFile(path.join(directory, entry.data.url)));
    value.comet_contract_version = 1;
    if (['1P', '2P', '55P'].includes(entry.id)) validateComet(value, entry.id);
    else assert.throws(() => validateComet(value, entry.id), /identity/);
  }
});
