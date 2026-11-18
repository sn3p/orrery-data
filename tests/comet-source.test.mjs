import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import CometSource, { interpolateComet } from '../consumer/CometSource.js';

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
  await fixture(t, async relative => {
    if (relative.startsWith('objects/')) await new Promise(resolve => { release = resolve; });
  });
  const source = await CometSource.openLatest(base + 'latest.json');
  const controller = new AbortController();
  const pending = source.load('1P', { signal: controller.signal });
  controller.abort(); release();
  await assert.rejects(pending, { name: 'AbortError' });
  const pending2 = source.load('1P'); source.close(); release();
  await assert.rejects(pending2, { name: 'AbortError' });
  assert.equal(source.cache.size, 0);
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
