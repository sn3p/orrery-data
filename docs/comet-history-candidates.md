# Unpublished historical comet candidates and Orrery handoff

This is a candidate contract, not the hosted product. `data/`, its schema-2
producer, site staging and hosted latest remain unchanged. No deployment or
Orrery application changes are part of this work. Discovery-based visibility is
**app-owned**; event metadata does not hide objects or authorize extrapolation.

## Build and verify

Restore the verified raw research cache into `.context/comet-history-pilot/raw/`
as described in the [research README](../research/comet-history/README.md).
No network acquisition occurs in this command. Run from the repository checkout:

```sh
python3 scripts/comet_history_candidate.py --output .context/comet-candidates/history-v3
python3 scripts/comet_history_candidate.py --output .context/comet-candidates/history-v3 --verify
COMET_HISTORY_CANDIDATE=.context/comet-candidates/history-v3 npm run test:comets
```

Output must be a named directory under `.context/comet-candidates/`; aliases into
public data are rejected before replay. Candidates are immutable: an existing
path is verified, never overwritten. A new path is staged and renamed only after
validation. There is no install, refresh-online or publish option. Raw cache and
research sources remain outside the candidate. The script is a checkout tool,
not part of the installed `orrery-data` CLI.

Verification replays the retained source evidence and compares the complete file
inventory and exact bytes. It therefore requires raw inputs, not just the candidate.
Each build validates modern schema 2, exact six-object membership, raw discovery
assertions, strict metadata, historical target/solution/model identity, all final
serialized historical interval quarter points, and position plus analytic velocity
at the seam and support-overlap endpoint. Changed historical target or modern
solution requires reassessment. Retained provenance includes URLs, raw hashes,
retrieval clocks, API version and scientific headers.

## Wire contract

UTF-8 JSON with trailing newline; hashes and limits describe decoded bytes.
The descriptor is `{comet_contract_version: 3, index: {url, sha256, bytes}}`.
Index is `{comet_contract_version: 3, sources, objects}`. Descriptor ≤4 KiB,
index ≤256 KiB, each trajectory ≤2 MiB. References use the existing hash filename,
relative path, integrity and HTTPS rules.

Each catalogue entry has exactly `id`, `name`, `discovery`, `segments`, `transition`.
`discovery` preserves the research object: raw `catalogue_discovery`, separate
`events`, `catalogue_provenance`, stable `id`, and `event_inventory_complete: false`.
The index `sources` resolves event citations. Original raw-file paths identify
retained research evidence; they are not candidate download references. Dates
preserve astronomical year numbering, calendar, year/month/day precision and null
unknown components. No January 1/day 1 is manufactured. The unknown SBDB retrieval
clock stays null. Preserve 10P's unresolved day conflict and all recovery/sighting,
independent-discovery, photographic and recognition distinctions.

Segments contain exactly `key`, `payload_contract_version`, `data`,
`sample_coverage`, `coverage`, `provenance`, `validation`. `coverage` is
`{start, end, end_inclusive}` in UT Julian days; start is always inclusive.
Modern payloads are the existing schema-2 object bytes, with unchanged provenance
and midpoint validation. Historical payloads have exactly
`{comet_contract_version: 3, id, segment: "historical", conventions, series}`.
Conventions retain `uniform-state-v1`; series is `{start, step: 4, states}` with
six finite numbers per row and 12 significant digits. Unknown fields/versions,
incorrect grids/counts, unsupported segments and ambiguous coverage are rejected.

| Object | Historical owned interval | Samples | Modern owned interval |
|---|---|---|---|
| 1P | [1835-08-21, 1980-01-01) | 13,183 through 1980-01-01 | [1980-01-01, 2099-12-30] |
| 55P | [1865-12-22, 1980-01-01) | 10,413 through 1980-01-02 | [1980-01-01, 2099-12-30] |
| 2P, 9P, 10P, 67P | None | None | [1980-01-01, 2099-12-30] |

**Modern owns JD2444239.5 (1980-01-01 00:00 UT) onward.** 55P's January 2
sample supports interpolation before the seam only. There is no rephasing,
cross-file Hermite interval, trajectory blend or extrapolation. Modern coverage
ends at JD2488067.5, not the shorter full 55P research pilot endpoint.

Historical validation retains three independent quarter-point reports and
representative first/worst/last references for each grid. Transition records
contain `jd`, `owner: "modern"`, position/velocity limits and measured
`comparisons` at T and, for 55P, January 2. Limits are 0.000052 AU and
0.000013 AU/day. These are numerical compatibility guards, not physical uncertainty
or a continuous interpolation bound. Reader loads both members of a transition pair and checks actual position/velocity
comparisons before returning either member, including historical-only sessions and
retries. A failing pair evicts both cached payloads.

## Reference reader API

Copy `CometHistorySource.js`, `cometHistoryContract.js`, `cometSourceCore.js`,
`contract.js`, `transport.js` and the MIT attribution/license. Keep
`CometCatalogSource.js` for schema-2 rollback/transition support. The schema-2
adapter remains strict: do not route a schema-3 descriptor to it.

```js
const source = await CometHistorySource.openLatest(candidateURL, { signal });
const entry = source.info.objects.find(object => object.id === '55P');
const segment = source.segmentAt('55P', jd); // null outside owned coverage
const position = await source.positionAt('55P', jd, { signal }); // xyz or null
const strips = await source.pathSegments('55P', firstJD, lastJD, { signal });
source.close();
```

`load(id, {jd, signal})` requires an explicit finite date and returns the owning
payload or null. `loadSegment(id, key, {signal})` is a low-level explicit read;
its sample support is not public coverage. Use `positionAt` for markers.
`pathSegments` returns separate `{key, points: [[JD,x,y,z], ...]}` strips, clipped
to requested and owned intervals. A historical strip ends at the immediately preceding representable Julian day
below T; exact T belongs exclusively to the modern strip and marker. Do not connect unequal seam
positions. Empty/outside ranges yield no fabricated points. A failed required
file rejects the whole path result and cancels its sibling subscribers; unrelated
subscribers retain their own cancellation lifetime. Applications must not present
partial Fit.

Metadata loads first, trajectories lazily. One pin owns all cache and pending
loads. Concurrent subscribers share work; one cancellation preserves other
subscribers, the last cancellation aborts shared work, and retry never changes
pin. Failed reads are not cached. Close aborts pending/queued reads. The adapter
allows two simultaneous downloads. The strict fixed catalogue permits only eight
payloads of at most 2 MiB each, so retaining the entire verified inventory is bounded
by 16 MiB decoded bytes; there is no unused eviction policy. JSON/array heap overhead and app-retained
references are additional; this is not a heap budget or measured UI performance.

## Concrete Orrery integration work

Reconcile current Orrery master first. At producer assessment, master
`d2d3957afba07f93b1d534821ac777d4119214bd` includes PR115's unversioned URL and
PR116's chooser. Integrate in a fresh Orrery workspace, not this producer worktree.

1. Replace the single-series assumption in `comets/Acquisition.js` and `model.js` with a pinned segmented model. Keep schema-2 support; add an explicit candidate fixture/input for tests. Preserve generation guards, selection, deselection, retry/close and renderer switching. Never use historical samples to extend unsupported coverage or silently mix fallback/hosted solutions.
2. Add metadata to timeline handling without inventing exact dates. Decide discovery-based visibility in Orrery; producer makes no such choice. Distinguish unsupported coverage, loading, available and failed states in the options panel and chooser, including retry and selection persistence.
3. Update `pixi/Comets.js`, `three/Comets.js`, shared path-window helpers and `viewFit.js`. Shift the display window over the contiguous supported coverage, then request all intersecting segments. Draw separate strips and Fit those same points plus the marker. Hidden paths must still give the same Fit. Preserve existing period/window presentation choices.
4. Put this exact copy in comet Options, with accessible association and no stale blanket 1980–2099 hint:

   > Comet paths are calculated from JPL orbit models. Historical positions are approximate, with uncertainty varying by comet and date.

5. Verify actual accepted payload → acquisition → both renderers and fresh state, historical starts, T−epsilon/T/T+epsilon, January 2 support overlap and December 30 end, timeline jumps, selection, path toggle/Fit parity, missing files, cancellation, retries, new pin/close, keyboard/focus and representative narrow/wide layouts. No app acceptance is claimed by producer tests.

Rollout is a separate reviewed action: ship a dual-version Orrery reader first,
then approve the hosted switch, retention/pruning and old-client behavior. Existing
site publication only supports schema 2 and is deliberately unchanged. Preserve
schema-2 pin/assets and the app's separately identified bundled rollback. Neither
ancient trajectories, other-four historical extensions nor Orrery3D are implied.

## Evidence and test boundaries

The committed index fixture captures the candidate schema/provenance; ordinary
reader tests replace trajectory bytes with synthetic zero states and matching
references, exercising strict serialization/transport boundaries without raw
network evidence in Git. Setting `COMET_HISTORY_CANDIDATE` also exercises all real
files, representative references, seam comparisons and exact modern byte reuse.
Full raw scientific replay is a local retained-evidence check, not a CI claim.
