# Named reference bodies, contract 1 (unpublished)

OrreryData owns one scientific product for Mercury, Venus, Earth, Mars, Jupiter,
Saturn, Uranus, Neptune, Ceres, Pluto, Haumea, Makemake and Eris. The prototype has
been implemented and tested locally; it is **not hosted, activated or integrated
into Orrery**. Existing asteroid/comet publication inventory and bytes are unchanged.

The product uses 12 fitted numbers per body, rather than sampled trajectories.
`research/named-bodies/definitions.json` is the reviewed scientific input to the
candidate builder. Its compressed numerical evidence is a development asset,
not a required browser download. No source defaults to an independently authored
six-planet dataset.

## Scientific contract

Definitions have `named_body_version: 1`, shared `conventions`, `model`,
`validation_interval`, `extrapolation`, `evidence_sha256` and exactly 13 `bodies`.
Each body has stable `id`, `name`, `classification`, Horizons `target`,
`centre_kind`, six `elements`, six `rates`, three `sources`, and `validation`.

- Planet IDs use the producer-owned `orrery:planet:<lowercase-name>` namespace.
  This is a curated OrreryData identifier, not an IAU/NAIF authority claim.
- Dwarfs retain MPC IDs: `mpc:number:1`, `134340`, `136108`, `136472`, `136199`
  for Ceres, Pluto, Haumea, Makemake and Eris respectively. The MPC identity for
  Pluto is deliberately distinct from its Horizons ephemeris target `9`.
- Classification is curated planet/dwarf-planet taxonomy, independent of asteroid
  orbital-population groups. See [NASA's dwarf-planet overview](https://science.nasa.gov/dwarf-planets/).
- Heliocentric geometric XYZ positions, AU, degrees and TDB Julian days. Epoch
  JD2451545.0 TDB; rates per 36525-day Julian century. Frame: Horizons ecliptic
  J2000, using IAU76 obliquity relative to ICRF. No renderer projection is baked in.
- Element order: semimajor axis `a`, eccentricity `e`, inclination `i`, ascending
  node longitude, **argument** of perihelion, mean anomaly. These are empirical
  linear-rate fits to Horizons vectors, not JPL-published approximate coefficients.
- Mercury199, Venus299 and Earth399 use body centres. Mars4, Jupiter5, Saturn6,
  Uranus7, Neptune8 and Pluto9 use system barycentres. Ceres1;, Haumea136108;,
  Makemake136472; and Eris136199; use the named small-body orbital solutions.
  These approximations are intentional and explicit, not a claim that planet
  centres and barycentres are identical. Earth is Earth-centre, not Earth/Moon barycentre.

At date `jd`, evaluate each element as `element + rate * (jd-2451545)/36525`,
solve Kepler's equation and rotate by node, inclination and argument of perihelion.
The Python and JS implementations are checked independently against retained
[Horizons](https://ssd.jpl.nasa.gov/horizons/manual.html) reference vectors.

`validation_interval` is `[2378496.5,2488069.5]`: 1800-01-01 through 2100-01-01
TDB (inclusive). Each body records sampled maximum 3D position error in AU and
Sun-centred directional error in degrees, plus sample count. These are measured
residuals, **not guaranteed continuous error bounds or observational uncertainties**.

Outside this interval the reader returns `status: "approximate-extrapolation"`.
It freezes the five shape/orientation elements at the nearest boundary, while
continuing mean anomaly at the fitted rate. Position is continuous at each
boundary; the model remains elliptic and bodies continue moving. This avoids
unbounded fitted rates eventually producing impossible orbits. It neither clamps
simulation time nor hides a body. Inside the interval status is `validated-interval`;
it describes the tested interval, not a guarantee for every instant.

## Exact duplicate mapping

A candidate contains `index.json`, a content-addressed definitions file, and a
content-addressed mapping file. A trusted `{url,sha256,bytes}` reference to the
index pins both files. There is no automatic `latest` endpoint or activation path.

Mapping fields: `named_body_mapping_version: 1`, `catalog_id`,
`asteroid_index_sha256`, `snapshot_version`, `records`, and five `members`.
Each member has `id`, `status`, `ordinal`, `discovery_jd`, and `reason`.

Example member (ordinal is derived for a particular export, never hardcoded):

```json
{"id":"mpc:number:1","status":"present","ordinal":0,"discovery_jd":2378861.5,"reason":null}
```

`absent-from-export` has null ordinal and one of `not-in-master`,
`unknown-discovery-date`, or `selection-limit`. Discovery date is retained when
known. Failure to verify or join is an error, never an absence record.

The builder validates the mapping schema before installation. It captures identities
during the full export verification pass and compares hash-verified browser bytes
without reopening the index or chunks. It applies its known-date
selection limit **before** stable discovery-date sorting, compares every selected
row with the export and browser chunks, and joins the five exact MPC IDs. Ties
retain input order. Duplicate master IDs, wrong generations, missing/corrupt files
and a changed browser descriptor fail. It preserves all asteroid rows/counts.
A mapping cannot be reused with another index, even if ordinals happen to match.

## Build and read

Use an existing verified full export and its matching browser distribution:

```sh
python3 scripts/named_body_candidate.py \
  --export /path/to/export-v1-HASH \
  --browser /path/to/matching-browser-distribution \
  --output .context/named-body-candidates/review-name
```

The output must be a direct child of that isolated candidate namespace. Assembly
uses a writer lock and atomic directory installation. Repeating an identical build
is a no-op; a different generation cannot overwrite an existing candidate. A failed
build leaves the prior candidate intact. Use a new name for a reviewed refresh.
No site inventory or publication command accepts/installs this candidate automatically.

Copy `NamedBodySource.js`, `contract.js` and `transport.js` with the existing
`CatalogSource` package (see consumer/README.md). With a verified open asteroid source:

```js
import {loadNamedBodies, namedPosition} from './NamedBodySource.js';
const next = await loadNamedBodies(asteroids, trustedProductRef, productIndexURL,
  {signal});
const earth = next.definitions.bodies.find(b => b.id === 'orrery:planet:earth');
const {position: [x, y, z], status} = namedPosition(earth, jdTDB);
```

The resolved `trustedProductRef.url` must equal `productIndexURL`; absolute and
relative references are accepted, but a different host/path/query is rejected
before fetching. HTTPS is required except for loopback HTTP.

All payloads are bounded and hash-verified before the result is returned. Returned
objects are frozen. Loading needs the asteroid index, **not discovery chunks**.
Definitions can also be bundled/cached and passed to `validateNamedDefinitions`;
such copies must preserve exact producer bytes/provenance. Scientific definitions
remain independent of catalogue membership and discovery dates.

The consumer owns refresh sequencing: load the prospective asteroid source and
matching named product, then commit that pair together. On failure retain the
previous complete pair. Abort obsolete requests on refresh; a failed or cancelled
load returns no partial replacement. The reader links loading to the asteroid
source's lifetime and rejects mismatched pins. It does not mutate renderer state
or decide which concurrent request should win.

## Orrery migration and acceptance

Replace all six app-owned definitions in `src/js/planets.js` with this product's
13 bodies. Keep app-owned colours/sizes keyed by stable ID. Do not feed the fitted
coefficients into the old fixed-period orbit code: use the named propagation law
for positions and date-dependent orbit shape. Orbit geometry must use the same
boundary/extrapolation policy. Pixi projects `(-x,y)`; Three retains XYZ, then both
apply their existing world-unit scale.

All 13 belong to an always-present reference layer, independent of discovery dates,
chunks and asteroid group filters. Ordinary clipping and explicit display controls
still apply. Suppress only the five mapped anonymous render instances; do not
remove their producer rows, change raw discovery statistics, or count references
as additional discoveries. Consumer displayed-count wording remains app-owned.

Preserve opening cameras, zoom, group fits, view state across renderer switching,
labels/orbits and existing presentation. No new Fit action or automatic reframing.
The app should distinguish approximate extrapolation without disabling dates.
Any offline fallback must be generated from the same producer product; a separately
authored six-body scientific fallback is not the intended final arrangement.

Separate consumer acceptance remains required for Pixi/Three positions and orbit
lines, labels, filters, discovery boundaries/counts, clipping, switching/recovery,
failed/stale/concurrent refresh, offline fallback and unchanged camera behaviour.
Producer tests do not establish this application integration.

## Reproduction and verification

See [research evidence](../research/named-bodies/README.md). Run:

```sh
python3 scripts/refit_named_bodies.py
python3 -m unittest discover -s tests -p 'test_named_bod*.py' -v
node --test tests/named-bodies.test.mjs
node scripts/verify_named_bodies.mjs /candidate/directory /matching/browser/directory
```

The last command uses filesystem-backed fetch responses and the actual
`CatalogSource.open`/named-product readers; it starts no HTTP server. The Python
builder additionally checks every real asteroid chunk against the full export.

Initial local delivery used a freshly acquired unpublished MPC generation because
the identity-bearing inputs for the current hosted catalogue were unavailable.
Its exact pins/paths and local verification results are in the workspace consumer
handoff. It must not be mixed with the current hosted asteroid index. Publication,
activation and consumer integration are separate future steps.
