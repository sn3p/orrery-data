# Consumer handoff: inactive named bodies

**Producer ready for integration; activation is blocked until Orrery integration
is verified.** This PR does not change `data/`, publish named-body endpoints,
modify Orrery, or authorize deployment. Existing asteroid/comet products continue
as before. A separate reviewed activation step is required after app acceptance.

- Replace all six app-owned planet definitions with the producer's **all-13**
  product. Keep colours/sizes in Orrery, keyed by stable ID. Generated/cached
  scientific fallback data must retain the same producer provenance.
- Read `consumer/NamedBodySource.js` with the existing `CatalogSource`,
  `contract.js` and `transport.js`. `loadNamedBodies` returns an immutable pair of
  definitions and an exact asteroid-index-pinned dwarf mapping.
- Use `namedPosition(body, jdTDB)` and the same date-dependent elements for orbit
  lines. The old fixed-period orbit model cannot simply consume fitted rates.
  See [the complete contract](named-bodies.md) for frame, units, IDs and centre choices.
- Validation spans **1800-01-01–2100-01-01 TDB**, with 6,947 reference positions
  per body. Outside it, motion continues with explicit approximate-extrapolation
  status; do not restrict the selectable timeline. Errors are sampled measurements,
  not continuous guarantees. Ceres remains on the compact model (~0.027 AU maximum).
- All 13 are reference bodies independent of discovery dates/chunks and asteroid
  filters. Suppress the five mapped anonymous render instances only. Preserve
  raw discovery counts and metadata. Keep camera/zoom, group fits and renderer view
  state; ordinary clipping and user display controls still apply.

## Get a matching candidate

The maintained manual/scheduled update path, `scripts/update_browser.py`, now
regenerates an inactive companion **before installing** each asteroid generation.
It uses the exact verified full export and preserves the final browser pin, including
no-op acquisition timestamps. A companion failure aborts installation. Output JSON
contains `named_bodies.path`, the product index reference and `activation: inactive`.
Definitions-only changes produce a new companion without changing asteroid bytes.

Candidates live under `.context/named-body-candidates/refresh-<asteroid-pin>-<science-hash>`.
Scheduled runs retain them as **inactive-named-bodies-<run>-<attempt>** review artifacts
for 90 days, even for an unchanged catalogue. They are never staged in `data/`,
committed by the refresh workflow or included in Pages artifacts. The raw
`export-browser` projection primitive is lower-level: callers using it directly
must explicitly build the companion with `scripts/named_body_candidate.py`.
That path must not bypass pairing checks in any future activation design.

For a chosen existing full export/browser pair:

```sh
python3 scripts/named_body_candidate.py --export /full/export-v1-HASH \
  --browser /matching/browser --output .context/named-body-candidates/review
node scripts/verify_named_bodies.mjs .context/named-body-candidates/review /matching/browser
```

The initial local full-data candidate uses asteroid index SHA-256
`f83dd056cb36594475ccf82827fb07d5845d131387ea41c41fcf7c282cb300c8` and named product
index SHA-256 `ef3423709abd3436393059b835ba1c3db5f9543559a99009d1239a324c30b4a2`.
These identify an **unpublished** generation, not the current hosted catalogue.
Use both together or regenerate a complete matching pair; never reuse its ordinals
with another pin. Actual paths and retained raw inputs are in the local workspace
handoff, while committed evidence supports offline numerical reproduction.

## Required Orrery acceptance before activation

Verify Pixi and Three positions/orbit lines, labels, filters/counts, discovery
boundaries, ordinary clipping, switching/recovery, offline handling, stale/failed/
concurrent catalogue refresh, and unchanged cameras. Commit the asteroid/named
pair together only after both load successfully; retain the prior pair on failure.
Producer tests do not establish these app-level results. No standalone Orrery3D
rollout, comet discovery playback, automatic reframing or general-name lookup is
included. Track CI results separately from local verification.
