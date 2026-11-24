import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { pathToFileURL } from 'node:url';

test('documented consumer files load from an isolated copied package', async t => {
  const readme = await fs.readFile('consumer/README.md', 'utf8');
  const files = readme.match(/```text\n([^`]+)```/)[1].trim().split('\n');
  const directory = await fs.mkdtemp(path.join(os.tmpdir(), 'orrery-consumer-copy-'));
  t.after(() => fs.rm(directory, { recursive: true, force: true }));
  await fs.writeFile(path.join(directory, 'package.json'), '{"type":"module"}\n');
  for (const file of files) {
    assert.match(file, /^[A-Za-z]+\.js$/);
    await fs.copyFile(path.join('consumer', file), path.join(directory, file));
  }
  const { default: CatalogSource, fetchVerified } = await import(pathToFileURL(path.join(directory, 'CatalogSource.js')));
  assert.equal(typeof CatalogSource.openLatest, 'function');
  const { default: CometCatalogSource } = await import(pathToFileURL(path.join(directory, 'CometCatalogSource.js')));
  assert.equal(typeof CometCatalogSource.openLatest, 'function');
  const { default: History } = await import(pathToFileURL(path.join(directory, 'CometHistorySource.js')));
  assert.equal(typeof History.openLatest, 'function');
  assert.equal(fetchVerified.length, 3);
});
