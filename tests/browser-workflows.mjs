import { chromium, firefox, webkit } from "playwright";
import assert from "node:assert/strict";
import path from "node:path";
import fs from "node:fs/promises";
import { serve } from "./http-server.mjs";

const app = await serve(path.resolve("."));
const data = await serve(path.resolve("tests/fixtures/browser-v1/ties"), { compressed: true });
const reports = [];
await fs.mkdir(".context/browser-checks", { recursive: true });
try {
  for (const [name, engine] of Object.entries({ chromium, firefox, webkit })) {
    const browser = await engine.launch();
    try {
      const page = await browser.newPage({ viewport: { width: 1280, height: 800 } });
      const pageErrors = [], consoleErrors = [], expectedResourceErrors = [];
      let removedOldFiles = false;
      page.on("pageerror", error => pageErrors.push(error.message));
      page.on("console", message => {
        if (message.type() !== "error") return;
        const error = { text: message.text(), url: message.location().url };
        if (removedOldFiles && error.url.startsWith(data.origin + "/chunks/") && /404|Not Found/i.test(error.text)) {
          expectedResourceErrors.push(error);
        } else consoleErrors.push(error);
      });
      const go = () => page.goto(`${app.origin}/tests/browser-harness.html?latest=${encodeURIComponent(data.origin + "/latest.json")}`);
      data.state.root = path.resolve("tests/fixtures/browser-v1/ties");
      data.state.delay = 0; data.state.faults.clear();
      await go();
      const untrustedRequests = [];
      page.on("request", request => {
        if (new URL(request.url()).hostname.endsWith("example.test")) untrustedRequests.push(request.url());
      });
      for (const url of ["http://catalogue.example.test/latest.json", "http://localhost.example.test/latest.json"]) {
        const error = await page.evaluate(async url => {
          const { default: CatalogSource } = await import("/consumer/CatalogSource.js");
          try { await CatalogSource.openLatest(url); return null; }
          catch (error) { return error.message; }
        }, url);
        assert.match(error, /HTTPS required outside loopback/);
      }
      assert.deepEqual(untrustedRequests, []);
      await page.getByRole("button", { name: "Load catalogue", exact: true }).focus();
      await page.keyboard.press("Enter");
      await page.waitForFunction(() => window.result.state === "complete");
      assert.equal(await page.evaluate(() => window.result.committed), 6);
      const before = await page.evaluate(() => window.result.sourceId);
      await page.screenshot({ path: `.context/browser-checks/${name}-desktop.png` });
      await page.setViewportSize({ width: 375, height: 812 });
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
      await page.screenshot({ path: `.context/browser-checks/${name}-narrow.png` });
      data.state.delay = 200;
      await page.getByRole("button", { name: "Reload latest" }).click();
      await page.getByRole("button", { name: "Cancel", exact: true }).click();
      await page.waitForFunction(() => window.result.state === "cancelled");
      // Open old index, then remove its files while requests are pending.
      await page.getByRole("button", { name: "Reload latest" }).click();
      await page.waitForFunction(() => !!window.result.sourceId);
      removedOldFiles = true;
      data.state.root = path.resolve("tests/fixtures/browser-v1/empty");
      await page.waitForFunction(() => window.result.state === "error");
      assert.equal(await page.evaluate(() => window.result.committed), 0);
      await page.getByRole("button", { name: "Reload latest" }).click();
      await page.waitForFunction(() => window.result.state === "complete");
      assert.equal(await page.evaluate(() => window.result.committed), 0);
      assert.notEqual(await page.evaluate(() => window.result.sourceId), before);
      removedOldFiles = false;
      assert.deepEqual(pageErrors, []);
      assert.deepEqual(consoleErrors, []);
      reports.push({ browser: name, discoveryTrust: "passed", fixtures: "passed", responsive: "passed", recovery: "passed", pageErrors, consoleErrors, expectedResourceErrors });
      if (name === "chromium" && process.env.FULL_BROWSER_DATA) {
        data.state.root = path.resolve(process.env.FULL_BROWSER_DATA);
        data.state.delay = 0;
        data.state.calls.length = 0;
        await page.getByRole("button", { name: "Reload latest" }).click();
        await page.waitForFunction(() => window.result.state === "complete", null, { timeout: 120000 });
        const result = await page.evaluate(() => window.result);
        const latest = JSON.parse(await fs.readFile(path.join(data.state.root, "latest.json")));
        const index = JSON.parse(await fs.readFile(path.join(data.state.root, latest.index.url)));
        assert.equal(result.committed, index.counts.discovery_export);
        assert.equal(new Set(data.state.calls.filter(x => x.path.startsWith("/chunks/")).map(x => x.path)).size, index.chunks.length);
        assert.equal(data.state.calls.some(x => x.path.includes("full/")), false);
        assert.deepEqual(pageErrors, []);
        assert.deepEqual(consoleErrors, []);
        await page.screenshot({ path: ".context/browser-checks/full-catalogue.png" });
        reports.push({ browser: name, full: result, chunks: index.chunks.length });
      }
    } finally { await browser.close(); }
  }
} finally {
  await app.close(); await data.close();
  await fs.writeFile(".context/browser-checks/results.json", JSON.stringify(reports, null, 2) + "\n");
}
console.log(JSON.stringify(reports, null, 2));
