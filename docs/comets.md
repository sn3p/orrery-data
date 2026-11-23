# Comet browser contract v1

OrreryData now has a separate, manually refreshed comet product. Asteroid
`latest.json`, discovery ordering, rows and weekly schedule remain compatible.
The initial reviewed selection is 1P/Halley, 2P/Encke and 55P/Tempel-Tuttle.
Orrery v1 adoption shipped in PR110 (`f55f237b`). The separate
[six-object v2 catalogue](comets-v2.md) retains this endpoint unchanged;
Orrery v2 adoption remains separate work. No comet schedule
is enabled in this milestone.

## Commands and ownership

```sh
# First acquisition: six serial Horizons queries; subsequent calls reuse saved responses.
python3 -m orrery_data refresh-comets
# Explicitly reacquire all six queries after deciding an upstream check is needed.
python3 -m orrery_data refresh-comets --refresh
# Rebuild and validate with no network access (requires the saved raw cache).
python3 -m orrery_data refresh-comets --offline
python3 -m orrery_data verify-comets --directory data/comets
# Verify the complete site; asteroid-only verification remains verify-browser.
python3 -m orrery_data verify-site --directory data
python3 scripts/prepare_browser_site.py --output .context/pages-site
```

`--cache` defaults to ignored `.data/horizons`; `--output` to `data/comets`.
The cache must be disjoint from the entire public site (the parent of the comet
output), including sibling product directories and resolved aliases. Invalid
overlap is rejected before any lock or input is written. Keep this cache for
offline regeneration. It retains exact HTTP bodies named by
SHA-256 and query records with retrieval timestamps. Saved queries are explicitly
reused, without an implied freshness guarantee; `--offline` rejects missing input.
The public projection can be verified without the raw cache, but cannot reproduce
all independent midpoint checks from its representative reference subset alone.
Ordinary builds and tests never query JPL.

A cache lock excludes competing access. Product refreshes share a per-site
Horizons acquisition lock across versions and caches; competing commands fail
fast and can be retried after the current acquisition. A separate per-output comet refresh lock
prevents older acquisitions from overtaking newer ones, including across caches.
Paths are resolved before deriving locks and staging destinations. Atomic
installation requires the site, comet tree and its existing files to share the
staging parent’s filesystem. Different filesystems are rejected before acquisition;
place the site inside a directory on the destination volume so its parent can
hold staging files on that same volume. This preserves pointer-last atomic
renames without placing temporary candidates in the public tree. When using
`data/comets`, both producers and the Pages stager share `.data-browser-lock`
during short snapshot and installation phases. Acquisition and full comet
verification leave it free; the product refresh lock prevents competing comet writers.
Build/validate the whole comet
candidate before installation; install immutable assets, then atomically replace
`latest.json`, then prune obsolete managed files. Failure before installation
preserves the previous projection. An interrupted install can leave managed
orphans: rerun the command to finish pruning. Public staging rejects those orphans
until recovery. Asteroid inventory/export and `verify-browser` do not read comet
payloads, so abandoned comet files cannot block asteroid generation. The
`verify-site` boundary validates each product once and rejects incomplete or
corrupt comet trees before deployment. A valid pointer always references already-installed bytes;
the authoring directory is not a live server. Unknown files and symlinks are
rejected, never deleted. The existing non-force Git push still rejects stale
scheduled MPC candidates; no stale generated output is rebased automatically.

No additional service or hosting is needed. Existing Pages path filters already
cover `data/**`, `orrery_data/**` and the staging script. Every deployment verifies
and copies the complete site. Reconciliation checks both descriptors, including
missing/stale remote comets when asteroids are unchanged. A later MPC refresh
can therefore retry a failed comet deployment without harvesting comets again.
An asteroid refresh never changes comet files. A comet refresh never changes
asteroid files. Removing the comet product is not a supported refresh operation. A deliberate
whole-product removal in a reviewed site commit is detected by remote/local
presence comparison and publishes the complete remaining site.
Future comet scheduling must coordinate with the existing data-writer workflow
and retain its data-only validation and non-force-push checks.

## Wire format

All files are UTF-8 JSON with one trailing newline. Hash and byte counts describe
**decoded bytes**, independent of HTTP compression. Object and index references
contain exactly `url`, `sha256` (lowercase hex) and `bytes` (positive integer).
Resolve index URLs against the descriptor URL, then object URLs against the index
URL, including when hosted below a subpath. The filename must contain its exact
content hash. No arbitrary paths, redirects for discovery, credentials or
non-HTTPS discovery outside loopback are supported by the reference consumer.

```text
comets/latest.json                  # <= 4 KiB
comets/index-<sha256>.json           # <= 256 KiB
comets/objects/<sha256>.json         # <= 2 MiB per object
```

Descriptor: exactly `{comet_contract_version: 1, index: <reference>}`.
Index: exactly `{comet_contract_version: 1, objects: [...]}`.
There are exactly three entries in order `1P`, `2P`, `55P`. Each entry has exactly:

- `id`: stable periodic-comet designation; `name`: full source-backed name.
- `coverage`: `[2444239.5, 2488067.5]`, actual inclusive UT Julian days.
- `data`: object reference.
- `provenance`: `vectors` and `midpoints`, each with the exact query `url`, raw
  response `sha256`, UTC `retrieved_at`, `target`, `solution`, `api_version` and
  retained scientific `header`.
- `validation`: `method` (`all-midpoints-final-serialized`), `samples` (10957),
  `max_error_au`, `worst_jd`, `tolerance_au` (0.000052), and independent
  `references` containing `[JD, x, y, z]`, including the worst case and endpoints
  of the midpoint grid.

Each object has exactly `comet_contract_version`, `id`, `conventions`, `series`.
Conventions are exactly:

```json
{"center":"Sun","frame":"Ecliptic J2000","time":"UT","position_units":"AU","velocity_units":"AU/day","correction":"NONE","interpolation":"cubic-hermite","encoding":"uniform-state-v1"}
```

Series is exactly `{start: 2444239.5, step: 4, states: [...]}` with 10,958 rows of
six finite numbers `[x,y,z,vx,vy,vz]`, rounded to 12 significant digits. This v1
seed contract deliberately fixes selection/grid; additions, nonuniform grids,
new frames or different coverage need an explicitly revised contract and reader.
Unknown versions/fields, wrong identities, units, counts, coverage, corrupt bytes
and mismatched inventories fail verification. No source values are inferred from
missing metadata. App colors, selection defaults and approximate display-window
periods remain app-owned; there is no producer claim of a closed orbit or tail.

## Scientific interpretation and evidence

The source is [JPL Horizons](https://ssd-api.jpl.nasa.gov/doc/horizons.html):
`VECTORS`, `CENTER=500@10`, `REF_PLANE=ECLIPTIC`, `REF_SYSTEM=ICRF`, `TIME_TYPE=UT`,
`OUT_UNITS=AU-D`, `VEC_TABLE=2`, `VEC_CORR=NONE`. Target and returned solution are
checked. The request stops at 2100-01-01, but the actual final sample is
**2099-12-30**. Cubic Hermite interpolation uses supplied positions and velocities;
extrapolation is rejected. Interpolation is not physical ephemeris uncertainty.
These are fitted dynamical predictions, not observations at each date or proof
that a comet remains physically intact throughout the interval.

The initial 2026-11-18 acquisition measured every interval midpoint against a
separate Horizons query, using the final serialized numbers:

| Object | Solution | Maximum measured difference (AU) | Object decoded bytes |
|---|---|---:|---:|
| 1P | JPL#75 | 0.000003219965082 | 1,088,469 |
| 2P | JPL#K273/30 | 0.000050590509925 | 1,087,667 |
| 55P | JPL#J985/69 | 0.000000259457677 | 1,099,322 |

The common 0.000052 AU acceptance target preserves the existing educational
visualization contract. It is a measured midpoint limit, not a continuous bound.
Four-day spacing must be reevaluated for future targets. The trajectory polyline
uses sampled integrated positions; the marker interpolation tolerance does not
establish screen-space polyline accuracy. Rendered appearance and app display
windows remain to be checked during Orrery migration.

The complete product is 3,311,911 decoded bytes; independently gzip-compressing
its five files measures 1,360,036 bytes (local measurement, not hosted transfer).
Only selected object payloads need downloading. There are no date chunks.

Raw response hashes and retrieval clocks are acquisition identity. Scientific
identity retains target/solution, API version, query, all scientific headers,
model/EOP details, serialized state bytes and validation results. Only operational
request banners are removed from the normalized header. If all scientific data
is equal, preserve the prior public acquisition provenance, while the cache
records the newest raw response. A changed solution/header/model remains a real
public change even when rounded states match. In particular, an EOP file or
coverage change deliberately produces a new pin; equal numeric rows alone do not
establish equal scientific provenance. Regression coverage exercises actual
date-stamped EOP header changes. There is no blanket removal of dates.

Live JPL returned API 1.2 while its documentation lists 1.3 on 2026-11-18. Both
explicit versions are accepted and recorded; unknown versions fail. Acquisition
uses a product/version/contact User-Agent, bounded responses and timeouts, three
attempts with exponential backoff on transient failures, and no simultaneous
requests, following [JPL fair use](https://ssd-api.jpl.nasa.gov/doc/).

## Consumer migration

Copy `consumer/CometSource.js`, `consumer/cometSourceCore.js`,
`consumer/contract.js` and `consumer/transport.js`
together; see the [consumer migration files and attribution](../consumer/README.md).
`consumer/CometSource.js` is a reference adapter using existing verified-fetch,
strict JSON and immutable metadata helpers. Open once per source generation:

```js
import CometSource, { interpolateComet } from './consumer/CometSource.js';
const source = await CometSource.openLatest(`${baseURL}/comets/latest.json`, { signal });
const comet = await source.load('1P', { signal });
const position = interpolateComet(comet.series, 2461362.5);
source.close(); // abort pending work and release cached objects
```

Descriptor and index load first; objects load only when selected. Concurrent
loads of one object share the download and verification. Cancelling one caller
does not cancel other subscribers; cancelling all callers aborts shared work
and permits an immediate retry. Failed or wholly cancelled loads do not enter
the cache. `close()` invalidates outstanding reads.
The caller owns selection/renderer generation guards: a late response must not
re-enable an unselected comet. Retry under the same pin, or explicitly create a
new source and replace the generation. Old hashed URLs may disappear after a
new deployment: offer reload/retry, never silently mix indices/solutions.

Keep Orrery's bundled fallback until migration is verified in both renderers.
Preserve independent asteroid/comet visibility, focus/path controls, selection
across renderer changes, boundary-shifted path windows and error/retry states.
The producer implementation does not claim that the app is migrated or hosted.

Tests: `python3 -m unittest discover -s tests -p 'test_comet*.py' -v` and
`npm run test:comets` run offline without servers or images. The committed
`data/comets` is the real numerical contract fixture. `tests/fixtures/horizons-1p.json`
is a three-row excerpt of a retained raw response used for malformed-response
regressions; its original headers deliberately retain the full requested range.
