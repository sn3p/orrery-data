// Shared bounded transport and cancellation for pinned browser data products.
import { requireValue, parseJSON, verifyBytes } from './contract.js';

export const aborted = () => new DOMException('Catalogue read cancelled.', 'AbortError');
export const check = signal => { if (signal?.aborted) throw aborted(); };
export const decodeJSON = bytes => parseJSON(new TextDecoder('utf-8', { fatal: true }).decode(bytes));

export function linkCancellation(controller, signals) {
  if (signals.some(signal => signal.aborted)) { controller.abort(); return () => {}; }
  const cancel = () => controller.abort();
  for (const signal of signals) signal.addEventListener("abort", cancel, { once: true });
  return () => { for (const signal of signals) signal.removeEventListener("abort", cancel); };
}

export async function fetchVerified(ref, url, signal) {
  check(signal);
  const response = await fetch(url, { signal });
  if (!response.ok) throw new Error(`Catalogue request failed (${response.status}): ${ref.url}`);
  // Content-Length can describe compressed HTTP bytes. Enforce the trusted
  // decoded length while streaming, before allocating/parsing an oversized body.
  const bytes = new Uint8Array(ref.bytes);
  const reader = response.body.getReader();
  let offset = 0;
  try {
    while (true) {
      check(signal);
      const { value, done } = await reader.read();
      if (done) break;
      requireValue(offset + value.length <= bytes.length, `byte length for ${ref.url}`);
      bytes.set(value, offset);
      offset += value.length;
    }
    requireValue(offset === bytes.length, `byte length for ${ref.url}`);
  } finally { await reader.cancel().catch(() => {}); reader.releaseLock(); }
  await verifyBytes(bytes, ref);
  check(signal);
  return bytes;
}


export async function fetchLatest(url, signal) {
  const location = new URL(url);
  // URL parsing normalizes IPv4 spellings and compressed IPv6 addresses.
  const loopback = ['localhost', 'localhost.', '[::1]'].includes(location.hostname)
    || /^127(?:\.\d{1,3}){3}$/.test(location.hostname);
  requireValue((location.protocol === 'https:' || (location.protocol === 'http:' && loopback))
    && !location.username && !location.password, 'latest URL (HTTPS required outside loopback)');
  check(signal);
  const response = await fetch(location.href, { signal, cache: 'no-cache', redirect: 'error' });
  if (!response.ok) throw new Error(`Catalogue discovery failed (${response.status}).`);
  const reader = response.body.getReader(), bytes = new Uint8Array(4096);
  let length = 0;
  try {
    while (true) {
      check(signal);
      const { value, done } = await reader.read();
      if (done) break;
      requireValue(length + value.length <= bytes.length, 'latest descriptor size');
      bytes.set(value, length); length += value.length;
    }
  } finally { await reader.cancel().catch(() => {}); reader.releaseLock(); }
  check(signal);
  return { location, latest: decodeJSON(bytes.subarray(0, length)) };
}
