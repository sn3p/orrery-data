# Curated comet catalogue v2

The supported comet product is `comets-v2/latest.json`, using
`comet_contract_version: 2` in descriptor, index and objects. See the
[wire format, scientific provenance and recovery contract](comet-contract.md).

## Catalogue contract

V2 accepts a nonempty catalogue with
unique numbered periodic-comet IDs (`[1-9][0-9]*P`) in producer-defined order.
Names must start with that ID plus `/`, have a nonempty suffix, no ASCII
control characters and no edge whitespace from the explicitly shared set in
`comet-identities.json` (including U+0085 and U+FEFF). Both runtimes use that
set rather than their different built-in trim rules. Provenance targets must
match the name; queries and object IDs must match the entry ID. Unknown schema
versions, fields, grids and invalid/duplicate identities fail closed.

The producer's reviewed selection currently contains `1P`, `2P`, `55P`, `9P`,
`10P`, `67P`. This selection is acquisition policy, not a reader allowlist.
Adding, removing or reordering reviewed objects within the existing conventions,
grid and size limits is a data update, not a new schema version. Each addition
still requires source-backed identity, complete coverage and independent numerical
validation. Different grids/conventions or structural changes require a reviewed
contract change. Consumers discover entries from `source.info.objects` rather
than assuming six objects or a fixed order; app colours/defaults remain app-owned.

## V1 retirement

Orrery's catalogue adoption and startup-switch fix shipped in PR111/PR112;
deployed Pixi and Three acceptance passed on 2026-11-23. The old `comets/`
product, v1 reader and `refresh-comets`/`verify-comets` commands are removed.
Breaking old clients is explicitly accepted; no compatibility redirect is provided.
Keep the existing v2 URL: retirement does not require another consumer migration.
Asteroid publication and Orrery's separate bundled rollback are unchanged.

A complete Pages artifact contains only asteroid data and the optional v2 catalogue.
Local verification rejects a leftover `comets/` directory. This retirement changes
publication code, so the existing merge workflow's `--publication-base` comparison
forces a complete-site replacement even when active data pins match. Routine
reconciliation requests only active product descriptors; it does not probe retired
URLs or infer deployment failure from cached legacy responses or redirects.

If the retirement deployment fails, rerun all jobs of that push workflow (which retains
its original publication base), or manually dispatch the publisher with `force=true`.
A later scheduled MPC run does not repair a retirement-only failure when active
pins already match. No remote deletion is claimed until an authorized publication
replaces the deployed site. CDN/browser caches and old visits may retain old bytes
until expiry; no purge or client audit is part of this change.

Historical v1 source/data remain in Git at `5d55c602e53cdb8edf9d75e19bdfa682d1f6a452`.
Original source headers and acquisition provenance remain in the current v2 index;
all-six independent midpoint evidence remains committed. Raw responses and prior
hosted acceptance evidence remain retained outside worktrees in local feature memory.

All six cover UT JD `[2444239.5, 2488067.5]`, 1980-01-01 through 2099-12-30,
with 10,958 states spaced four days apart. Horizons requests stop at 2100-01-01;
the actual last state defines coverage. Positions/velocities are Sun-centred,
Ecliptic J2000, AU and AU/day, UT, geometric (`NONE`). Cubic Hermite interpolation
rejects extrapolation. No closed-orbit, physical-survival, tail or continuous
error-bound claim is made. Interpolation differences are not physical uncertainty.
The initial v2 release preserved the original three series, provenance and
validation exactly; only their v2 object envelope/version (and therefore content
hash) changed. Subsequent explicit v2 refreshes may advance their scientific
solutions without a cross-version equality requirement.

## Selection and measured evidence

We evaluated three additional numbered periodic identities directly against
[JPL Horizons](https://ssd-api.jpl.nasa.gov/doc/horizons.html), using explicit
`DES=...; CAP;` queries and checking returned names/solutions. Tempel 1, Tempel 2
and Churyumov–Gerasimenko were selected because each supplied complete coverage
and passed all 10,957 independent interval midpoints on the existing grid.
No threshold relaxation or fabricated extension was needed. This is a curated
six-object release, not a claim that untested comets were unsuitable. All three
new vector/midpoint pairs were acquired serially on 2026-11-20; exact queries,
raw hashes, headers and retrieval times are retained in the index and cache.

| Object | Solution | Max midpoint error (AU) | Decoded object bytes |
|---|---|---:|---:|
| 1P/Halley | JPL#75 | 3.21996508175e-06 | 1,088,469 |
| 2P/Encke | JPL#K273/30 | 5.05905099252e-05 | 1,087,667 |
| 55P/Tempel-Tuttle | JPL#J985/69 | 2.59457676844e-07 | 1,099,322 |
| 9P/Tempel 1 | JPL#K284/3 | 1.03713198369e-07 | 1,085,489 |
| 10P/Tempel 2 | JPL#K265/65 | 1.21467185632e-07 | 1,088,318 |
| 67P/Churyumov-Gerasimenko | JPL#K284/1 | 1.35314241598e-07 | 1,105,120 |

Total: 6,627,126 decoded bytes; 2,770,744 independently gzip-compressed bytes. Local compression measurement, not hosted transfer.

Each maximum is measured from final serialized 12-significant-digit values.
Acceptance remains `0.000052 AU`. The committed gzip fixture contains every
independent midpoint position for all six objects, with original source metadata;
`test_comet_v2_evidence.py` checks grid coverage and exact reported maxima
against the current v2 product offline. A separate producer regression checks
that projection preserves supplied seed series/provenance; it does not require
future solutions to stay equal to a historical seed product.
Representative references remain in the public index for reference-reader checks. Full raw HTTP responses are retained
outside the public tree; the position fixture does not replace raw acquisition
responses for regeneration.

## Operation, failure and recovery

```sh
python3 -m orrery_data refresh-comets-v2 --offline
python3 -m orrery_data refresh-comets-v2       # acquire only missing cached inputs
python3 -m orrery_data refresh-comets-v2 --refresh  # explicitly reacquire twelve queries
# After any v2 refresh, regenerate the matching committed evidence offline.
python3 scripts/rebuild_comet_evidence.py
python3 -m orrery_data verify-comets-v2 --directory data/comets-v2
python3 -m orrery_data verify-site --directory data
python3 scripts/prepare_browser_site.py --output .context/pages-site-new
```

Commit refreshed `data/comets-v2` and `tests/fixtures/comet-v2-midpoints.json.gz`
together. The evidence command reads the exact raw SHA-256 files pinned by the
published index, so newer cache query records cannot silently substitute another
acquisition. It reparses both vector and independent midpoint responses, checks
provenance, serialized states, maxima/worst dates and public reference rows,
then atomically replaces the fixture. Missing/corrupt/mismatched inputs preserve
the previous fixture and fail; restore the retained pinned responses or finish a
valid explicit refresh before retrying. It never queries Horizons. Its optional
`--directory`, `--cache` and `--output` paths support retained offline inputs.

The default cache remains `.data/horizons`, and must be disjoint from the whole
site. The default output is `data/comets-v2`. Avoid custom nested product layouts:
the complete site recognizes only the v2 sibling product root. Ordinary builds,
tests and verification are offline. Missing/corrupt cache fails offline replay;
reacquire explicitly only when source refresh is intended.

The producer verifies the complete candidate before installation. Site locking,
separate output refresh locking, same-filesystem preflight, immutable assets
before pointer, unknown-file/symlink rejection and interrupted-orphan recovery
follow the [recovery contract](comet-contract.md). Rerun a failed refresh to prune managed orphans; unknown files require
operator investigation. A failed acquisition or numerical validation preserves
the previous product. Complete-site verification rejects corrupt/incomplete
products; asteroid-only refresh remains independent and preserves the v2 comet
tree. Publication compares both active descriptors, and stages the entire site on any difference. Nothing here deploys,
schedules refresh, changes the asteroid contract or removes bundled fallbacks.

## Consumer handoff

Copy `CometCatalogSource.js`, `cometSourceCore.js`, `contract.js`, `transport.js`
and the MIT license from the same revision.

```js
import CometCatalogSource, { interpolateComet } from './CometCatalogSource.js';
const source = await CometCatalogSource.openLatest(`${baseURL}/comets-v2/latest.json`, { signal });
const catalogue = source.info.objects; // identity, name, actual coverage, provenance
const comet = await source.load('67P', { signal });
const position = interpolateComet(comet.series, 2461362.5);
source.close();
```

V2 retains pinning, verified lazy loading, shared-request cancellation, cache
retry, close/disposal and no extrapolation. Consumers own selection and generation
guards; failures must expose retry/reload without mixing pins. Application acceptance covers selection, focus, window shifts, loading/error/retry,
cancellation and renderer switches. Bundled rollback remains an application decision.
No Orrery3D adoption, search UI, meteor streams or scheduling is included.

## Shared implementation

The Python catalogue entry module delegates to `comet_product.py` for schema checks,
interpolation, final-byte numerical validation and locked atomic installation.
The catalogue contract accepts membership independently of curated acquisition targets.
The JavaScript entry module uses `cometSourceCore.js` for validation, interpolation
and the pinned loading/cancellation lifecycle. These implementations remain in use.

Refresh coordination: outputs share a per-site Horizons acquisition lock,
including when different caches are used. The existing nonblocking writer-lock
policy rejects a competing acquisition before it can request data; retry after
the current refresh finishes. Product refresh locks prevent overtaking. Previous-product
verification, candidate construction and full candidate verification run outside
the site publication lock. Installation and verification of the installed product
hold that lock. The result describes the installed tree, independently of the
builder return value. Previous-product validation uses the captured descriptor
and returns its already-validated index without a second index read.
The descriptor is checked again before installation to reject stale preparation.
Inventory and filesystem preflight use one shared helper, called before acquisition,
after acquisition, and before installation to catch destination changes.
