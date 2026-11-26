// Resolve once; callers commit the returned immutable pair as one scene.
import {keys, requireValue, freeze, validatePin, MAX_INDEX_BYTES} from './contract.js';
import {fetchLatest, validatedURL} from './transport.js';
import CatalogSource from './CatalogSource.js';
import {loadNamedBodies} from './NamedBodySource.js';

export function validatePairedDescriptor(value) {
  keys(value, ['paired_catalogue_version', 'asteroids', 'namedBodies']);
  requireValue(value.paired_catalogue_version === 1, 'paired version');
  for (const [key, max] of [['asteroids', MAX_INDEX_BYTES], ['namedBodies', 128 * 1024]]) {
    const ref = value[key];
    keys(ref, ['url', 'bytes', 'sha256']);
    requireValue(typeof ref.url === 'string' && /^[a-f0-9]{64}$/.test(ref.sha256)
      && Number.isSafeInteger(ref.bytes) && ref.bytes > 0 && ref.bytes <= max, 'paired reference');
    const url = validatedURL(ref.url);
    requireValue(!url.hash && !url.search && url.pathname.endsWith(`/index-${ref.sha256}.json`), 'paired URL');
  }
  validatePin(value.asteroids);
  return freeze({pin: value.asteroids, namedBodies: value.namedBodies});
}

export async function resolvePairedLatest(url, {signal} = {}) {
  const {latest} = await fetchLatest(url, signal, 'paired catalogue');
  return validatePairedDescriptor(latest);
}

export async function openPairedLatest(url, {signal} = {}) {
  const selection = await resolvePairedLatest(url, {signal});
  const source = await CatalogSource.open(selection.pin, {signal});
  try {
    const namedBodies = await loadNamedBodies(source, selection.namedBodies, selection.namedBodies.url, {signal});
    return {source, namedBodies, selection};
  } catch (error) {
    source.close();
    throw error;
  }
}
