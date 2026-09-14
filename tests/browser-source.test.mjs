import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs/promises";
import path from "node:path";
import CatalogSource from "../consumer/CatalogSource.js";
import { serve } from "./http-server.mjs";

const root = path.resolve("tests/fixtures");
const cases = JSON.parse(await fs.readFile(path.join(root, "consumer-v1/cases.json")));
const read = async (source, range, options) => {
  const result = [];
  for await (const event of source.read(range, options)) result.push(event);
  return result;
};

test("latest discovery requires HTTPS outside loopback and rejects before fetching", async t => {
  const reachedFetch = new Error("reached fetch");
  const fetch = t.mock.method(globalThis, "fetch", async () => { throw reachedFetch; });
  for (const url of [
    "http://example.test/latest.json", "http://192.168.1.20/latest.json",
    "http://localhost.example.test/latest.json", "http://127.0.0.1.example.test/latest.json",
    "http://example.test/latest.json?next=http://localhost", "http://localhost@example.test/latest.json",
    "http://user@localhost/latest.json", "https://user@example.test/latest.json",
    "http://[::]/latest.json", "http://[::ffff:192.0.2.1]/latest.json", "ftp://localhost/latest.json",
  ]) await assert.rejects(CatalogSource.openLatest(url), /Invalid catalogue latest URL/, url);
  assert.equal(fetch.mock.callCount(), 0);
  for (const url of [
    "https://example.test/latest.json", "http://localhost:8080/latest.json", "http://LOCALHOST./latest.json",
    "http://127.0.0.1/latest.json", "http://127.25.50.75/latest.json", "http://127.1/latest.json",
    "http://[::1]/latest.json", "http://[0:0:0:0:0:0:0:1]/latest.json",
  ]) await assert.rejects(CatalogSource.openLatest(url), error => error === reachedFetch, url);
  assert.equal(fetch.mock.callCount(), 8);
});

test("actual adapter preserves v1 whole/indexed and new browser fixtures", async t => {
  const server = await serve(root, { compressed: true });
  t.after(server.close);
  for (const mode of ["whole", "indexed", "browser"]) {
    for (const name of ["ties", "empty"]) {
      const source = mode === "browser"
        ? await CatalogSource.openLatest(`${server.origin}/browser-v1/${name}/latest.json`)
        : await CatalogSource.open({ ...cases.bundles[name].pin, url: `${server.origin}/consumer-v1/${name}/index.json` }, { mode });
      for (const query of cases.queries.filter(item => item.bundle === name)) assert.equal(source.countThrough(query.through), query.requiredEnd);
      const expected = JSON.parse(await fs.readFile(path.join(root, `consumer-v1/${name}/full/catalog.json`)));
      for (const range of cases.reads.filter(item => item.bundle === name)) {
        const events = await read(source, range);
        assert.equal(events.at(-1).type, "complete");
        assert.deepEqual(events.filter(e => e.type === "batch").flatMap(e => e.records), expected.slice(range.start, range.end));
        assert.ok(events.every(event => event.sourceId === source.sourceId));
      }
      if (name === "ties") for (const invalid of cases.invalid_requests) {
        if (invalid.read) await assert.rejects(read(source, invalid.read));
        else assert.throws(() => source.countThrough(invalid.through));
      }
      source.close();
      await assert.rejects(read(source, { start: 0, end: 0 }));
    }
  }
  const latestCalls = server.state.calls.filter(call => call.path.endsWith("latest.json"));
  assert.ok(latestCalls.every(call => call.headers["cache-control"]?.includes("no-cache") || call.headers["cache-control"]?.includes("max-age=0")));
});

test("new sessions revalidate; old sessions fail coherently across replacement and reopen", async t => {
  const server = await serve(path.join(root, "browser-v1/ties"));
  t.after(server.close);
  const old = await CatalogSource.openLatest(server.origin + "/latest.json");
  server.state.root = path.join(root, "browser-v1/empty");
  const yielded = [];
  await assert.rejects(async () => {
    for await (const event of old.read({ start: 0, end: 6 })) yielded.push(event);
  });
  assert.equal(yielded.length, 0);
  const fresh = await CatalogSource.openLatest(server.origin + "/latest.json");
  assert.notEqual(old.sourceId, fresh.sourceId);
  assert.equal(fresh.info.counts.discovery_export, 0);
  assert.equal((await read(fresh, { start: 0, end: 0 })).at(-1).type, "complete");
  old.close(); fresh.close();
});

test("missing, corrupt, oversized and unsupported discovery are errors", async t => {
  const server = await serve(path.join(root, "browser-v1/ties"));
  t.after(server.close);
  for (const fault of [404, Buffer.from("x".repeat(4097)), Buffer.from('{"browser_contract_version":2,"index":{}}')]) {
    server.state.faults.set("/latest.json", fault);
    await assert.rejects(CatalogSource.openLatest(server.origin + "/latest.json"));
  }
  server.state.faults.clear();
  const source = await CatalogSource.openLatest(server.origin + "/latest.json");
  const chunk = source.info.chunks[0];
  for (const fault of [404, Buffer.from("x".repeat(chunk.bytes)), Buffer.alloc(chunk.bytes + 1)]) {
    server.state.faults.set("/" + chunk.url, fault);
    await assert.rejects(read(source, { start: 0, end: chunk.end }));
  }
  const latest = JSON.parse(await fs.readFile(path.join(root, "browser-v1/ties/latest.json")));
  await assert.rejects(CatalogSource.open({ ...latest.index, url: server.origin + "/" + latest.index.url }, { mode: "whole" }));
  source.close();
});

test("cancelling a read leaves another read usable; closing rejects stale work", async t => {
  const server = await serve(path.join(root, "browser-v1/ties"));
  t.after(server.close);
  server.state.delay = 50;
  const source = await CatalogSource.openLatest(server.origin + "/latest.json");
  const controller = new AbortController();
  const cancelled = read(source, { start: 0, end: 6 }, { signal: controller.signal });
  const other = read(source, { start: 1, end: 4 });
  controller.abort();
  await assert.rejects(cancelled, error => error.name === "AbortError");
  assert.equal((await other).at(-1).type, "complete");
  const stale = read(source, { start: 0, end: 6 });
  source.close();
  await assert.rejects(stale, error => error.name === "AbortError");
});
