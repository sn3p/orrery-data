import http from "node:http";
import fs from "node:fs/promises";
import path from "node:path";
import { gzipSync } from "node:zlib";

export async function serve(root, { compressed = false } = {}) {
  const state = { root, calls: [], faults: new Map(), delay: 0 };
  const server = http.createServer(async (req, res) => {
    const name = decodeURIComponent(new URL(req.url, "http://localhost").pathname);
    state.calls.push({ path: name, headers: req.headers });
    if (state.delay && name.includes("chunks/")) await new Promise(resolve => setTimeout(resolve, state.delay));
    res.setHeader("Access-Control-Allow-Origin", "*");
    res.setHeader("Cache-Control", "no-cache");
    try {
      const target = path.resolve(state.root, "." + name);
      if (!target.startsWith(path.resolve(state.root) + path.sep)) throw new Error("invalid path");
      const fault = state.faults.get(name);
      if (typeof fault === "number") { res.writeHead(fault); res.end(); return; }
      let bytes = fault instanceof Uint8Array ? fault : await fs.readFile(target);
      const ext = path.extname(target);
      res.setHeader("Content-Type", ext === ".json" ? "application/json" : ext === ".js" ? "text/javascript" : ext === ".html" ? "text/html" : "text/plain");
      if (compressed && ext === ".json" && req.headers["accept-encoding"]?.includes("gzip")) {
        bytes = gzipSync(bytes); res.setHeader("Content-Encoding", "gzip");
      }
      res.setHeader("Content-Length", bytes.length);
      res.end(bytes);
    } catch { res.writeHead(404); res.end(); }
  });
  await new Promise(resolve => server.listen(0, "127.0.0.1", resolve));
  return { ...state, state, origin: `http://127.0.0.1:${server.address().port}`,
    close: () => new Promise(resolve => { server.close(resolve); server.closeAllConnections(); }) };
}
