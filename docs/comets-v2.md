# Curated comet catalogue v2

This is the producer/reference-reader successor to [v1](comets.md). It is not an
Orrery selector or renderer migration. Reconciliation on 2026-11-20 confirmed
OrreryData PR10 merged at `9cee185`, Orrery PR110 at `f55f237b`, and hosted v1
assets matching the repository. Hosted Orrery still requires v1's exact three
objects. Earlier deployment-unconfirmed handoff statements are superseded.

## Compatibility and contract

`comets/latest.json` and every referenced v1 asset remain unchanged. V2 lives at
`comets-v2/latest.json`; it uses `comet_contract_version: 2` in descriptor, index
and objects. All other field names, strict validation, conventions, limits and
hash/relative-URL rules are those of v1. V2 accepts a nonempty catalogue with
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

## Transition to one supported product

The two endpoints are temporary migration scaffolding, not a public API support
commitment. The intended end state is one catalogue, one reader and one supported
schema. First publish this compatible producer change; next migrate both Orrery
renderers/selectors in a separate PR and verify the deployed app. Then remove the
v1 product, reader and obsolete commands in a reviewed cleanup. Keep the v2 URL
or explicitly coordinate a move back to `comets`; the final path is undecided.
A schema-version field can remain in the one product without hosting parallel
versions. This PR does not remove v1, migrate Orrery or authorize deployment.

All six cover UT JD `[2444239.5, 2488067.5]`, 1980-01-01 through 2099-12-30,
with 10,958 states spaced four days apart. Horizons requests stop at 2100-01-01;
the actual last state defines coverage. Positions/velocities are Sun-centred,
Ecliptic J2000, AU and AU/day, UT, geometric (`NONE`). Cubic Hermite interpolation
rejects extrapolation. No closed-orbit, physical-survival, tail or continuous
error-bound claim is made. Interpolation differences are not physical uncertainty.
The initial v2 release preserved the original three series, provenance and
validation exactly; only their v2 object envelope/version (and therefore content
hash) changed. Subsequent explicit v2 refreshes may advance their scientific
solutions independently of v1.

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
future v2 solutions to stay equal to the independently refreshed v1 product.
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
site. The v1 command remains unchanged and manages only `data/comets`.
V2 defaults to `data/comets-v2`. Existing v1 output rejects a v2 refresh rather
than being silently migrated. Avoid custom nested product layouts: the complete
site contract recognizes only these sibling product roots. Ordinary builds,
tests and verification are offline. Missing/corrupt cache fails offline replay;
reacquire explicitly only when source refresh is intended.

The producer verifies the complete candidate before installation. Site locking,
separate output refresh locking, same-filesystem preflight, immutable assets
before pointer, unknown-file/symlink rejection and interrupted-orphan recovery
follow v1. Rerun a failed refresh to prune managed orphans; unknown files require
operator investigation. A failed acquisition or numerical validation preserves
the previous product. Complete-site verification rejects corrupt/incomplete
products; asteroid-only refresh remains independent and preserves both comet
trees. Publication compares all three descriptors, including absent/removed
products, and stages the entire site on any difference. Nothing here deploys,
schedules refresh, changes the asteroid contract or removes bundled fallbacks.

## Consumer handoff

Copy `CometCatalogSource.js`, `cometSourceCore.js`, `contract.js`, `transport.js`
and the MIT license from the same revision. `CometSource.js` remains the strict v1 reader.

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
guards; failures must expose retry/reload without mixing pins. A subsequent
separate Orrery PR must make both selectors/renderers catalogue-driven and test
selection, focus, window shifts, loading/error/retry, cancellation and renderer
switches. Keep v1 and bundled rollback until that adoption is explicitly verified.
No Orrery3D adoption, search UI, meteor streams or scheduling is included.

## Shared implementation

Both Python entry modules delegate to `comet_product.py` for schema checks,
interpolation, final-byte numerical validation and locked atomic installation.
The v1 contract fixes the original selection; the v2 contract accepts catalogue
membership independently of the producer's curated acquisition targets. Both
JavaScript entry modules use `cometSourceCore.js` for validation, interpolation
and the pinned loading/cancellation lifecycle. Removing v1 later removes its
entry modules and data, while retaining the shared implementation.

Refresh coordination: both versions share a per-site Horizons acquisition lock,
including when different caches are used. The existing nonblocking writer-lock
policy rejects a competing acquisition before it can request data; retry after
the current refresh finishes. Product refresh locks prevent overtaking. Candidate
construction and full numerical verification run outside the site publication
lock; previous-product checks and installation verification retain that lock.
The descriptor is checked again before installation to reject stale preparation.
These checks protect different states and are intentionally retained.
